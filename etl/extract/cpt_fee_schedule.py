"""
CMS Physician Fee Schedule / CPT Code Extractor

Downloads the CMS Physician Fee Schedule RVU file and loads all active CPT codes
with descriptions, work RVUs, and surgical classification into the `cpt_codes` table.

Source: https://www.cms.gov/medicare/payment/fee-schedules/physician
File:   PPRRVU{YY}.zip  (published annually by CMS)
"""
from __future__ import annotations

import io
import zipfile

import httpx
import pandas as pd

from etl.common.config import get_logger
from etl.common.db import upsert_batch

logger = get_logger(__name__)

# CMS publishes a new RVU file each calendar year.
# Update the year suffix (YY) when CMS releases a new annual file.
CMS_RVU_URL = "https://www.cms.gov/files/zip/rvu26a-updated-12-29-2025.zip"

# CPT code ranges by procedure category.
# IMPORTANT: get_category() returns the FIRST match, so plastics must come
# before surgery to correctly classify overlapping ranges (e.g. rhinoplasty
# 30400-30462 sits inside the broad surgery respiratory range 30000-32999).
CPT_RANGES = {
    "plastics": [
        # Skin/tissue rearrangement — local flaps, Z-plasty, tissue expansion
        (14000, 14999),
        # Grafts, dermabrasion, chemical peel, blepharoplasty (15820-15823),
        # rhytidectomy/facelift (15824-15829), panniculectomy/body contouring
        # (15830-15839), suction lipectomy/liposuction (15876-15879)
        (15000, 15999),
        # Full breast range: augmentation (19325), mastopexy/lift (19316),
        # reduction mammaplasty (19318), implant exchange, reconstruction
        (19000, 19499),
        # Craniofacial/facial bone procedures: Le Fort osteotomies, mandible,
        # malar/cheek augmentation (21270), canthopexy (21280, 21282),
        # chin implants, craniofacial reconstruction
        (21120, 21299),
        # Rhinoplasty: primary (30400-30420), secondary (30430-30462),
        # septorhinoplasty, correction of nasal deformity
        (30400, 30462),
        # Cleft lip and palate repair
        (40700, 40761),
        # Eyelid: ptosis repair, entropion/ectropion correction, canthoplasty
        (67900, 67975),
        # Otoplasty (prominent ear correction)
        (69300, 69300),
    ],
    "surgery": [
        (10000, 13999),  # Integumentary (non-reconstructive skin/wound)
        (20000, 29999),  # Musculoskeletal
        (30000, 32999),  # Respiratory (non-rhinoplasty; rhinoplasty handled above)
        (33000, 37999),  # Cardiovascular
        (38000, 38999),  # Hemic/Lymphatic
        (39000, 39599),  # Mediastinum/Diaphragm
        (40000, 49999),  # Digestive
        (50000, 53899),  # Urinary
        (54000, 55899),  # Male Genital
        (55920, 58999),  # Female Genital
        (59000, 59899),  # Maternity
        (60000, 60699),  # Endocrine
        (61000, 64999),  # Nervous System
        (65000, 68899),  # Eye/Ocular (non-eyelid; eyelid handled above)
        (69000, 69979),  # Auditory (non-otoplasty)
    ],
    "scans": [
        (70000, 79999),
    ],
    "iv": [
        (90000, 99999),
    ],
}

# Body system by CPT range (first match wins — specific ranges before broad ones)
BODY_SYSTEM_MAP = [
    # Plastic-specific overrides — more specific, listed before broad ranges
    (14000, 14999, "integumentary"),
    (21120, 21299, "craniofacial"),
    (30400, 30462, "craniofacial"),   # rhinoplasty
    (40700, 40761, "craniofacial"),   # cleft lip/palate
    (67900, 67975, "eye"),
    (69300, 69300, "auditory"),
    # Broad ranges
    (10000, 19999, "integumentary"),
    (20000, 29999, "musculoskeletal"),
    (30000, 32999, "respiratory"),
    (33000, 37999, "cardiovascular"),
    (38000, 38999, "hemic_lymphatic"),
    (40000, 49999, "digestive"),
    (50000, 53899, "urinary"),
    (54000, 58999, "genital"),
    (59000, 59899, "maternity"),
    (60000, 60699, "endocrine"),
    (61000, 64999, "nervous"),
    (65000, 68899, "eye"),
    (69000, 69979, "auditory"),
    (70000, 79999, "radiology"),
    (80000, 89999, "pathology"),
    (90000, 99999, "medicine"),
]

# STATUS_CODE values that represent active, billable codes
ACTIVE_STATUS_CODES = {"A", "R", "T"}


def get_category(code_int: int) -> str | None:
    for category, ranges in CPT_RANGES.items():
        for low, high in ranges:
            if low <= code_int <= high:
                return category
    return None


def get_body_system(code_int: int) -> str | None:
    for low, high, system in BODY_SYSTEM_MAP:
        if low <= code_int <= high:
            return system
    return None


def download_rvu_zip(url: str) -> bytes:
    logger.info(f"Downloading CMS RVU file from {url}")
    response = httpx.get(url, timeout=120.0, follow_redirects=True)
    response.raise_for_status()
    logger.info(f"Downloaded {len(response.content) / 1024:.0f} KB")
    return response.content


def parse_rvu_zip(zip_bytes: bytes) -> pd.DataFrame:
    """Extract and parse the RVU file from the CMS ZIP.

    2026+: CMS ships a proper CSV alongside the fixed-width TXT — prefer it.
    Older years: fall back to the tab-delimited TXT with a single header row.
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        namelist = zf.namelist()

        # Prefer the CSV companion (e.g. PPRRVU2026_Jan_nonQPP.csv).
        # Sort so non-QPP comes before QPP when both are present.
        csv_files = sorted(
            [n for n in namelist if n.lower().endswith(".csv") and "pprrvu" in n.lower()],
            key=lambda n: "nonqpp" not in n.lower(),
        )

        if csv_files:
            filename = csv_files[0]
            logger.info(f"Parsing {filename} (CSV)")
            with zf.open(filename) as f:
                # Rows 0-8 are copyright / multi-level sub-headers; row 9 has column names.
                df = pd.read_csv(f, skiprows=9, header=0, dtype=str, low_memory=False)
        else:
            # Older format: tab-delimited TXT with a single header row (may have '#' comments).
            rvu_files = [
                n for n in namelist
                if n.lower().endswith(".txt") and "rvu" in n.lower()
            ]
            if not rvu_files:
                raise ValueError(f"No RVU file found in ZIP. Contents: {namelist}")
            filename = rvu_files[0]
            logger.info(f"Parsing {filename} (TXT)")
            with zf.open(filename) as f:
                df = pd.read_csv(f, sep="\t", dtype=str, comment="#", low_memory=False)

    # Normalize column names (CMS changes capitalization/spacing between years)
    df.columns = [c.strip().upper().replace(" ", "_") for c in df.columns]
    return df


def extract_codes(df: pd.DataFrame) -> list[dict]:
    """Transform the raw RVU DataFrame into cpt_codes records."""
    # Identify the right columns regardless of minor year-to-year naming changes
    col_map = {}
    for col in df.columns:
        # Stop as soon as we've found all four — avoids aliasing duplicate column names.
        if "code" not in col_map and col in ("HCPCS", "HCPCS_CODE"):
            col_map["code"] = col
        elif "description" not in col_map and col in ("DESCRIPTION", "SHORT_DESCRIPTION", "DESCRIPTION_SHORT"):
            col_map["description"] = col
        elif "status" not in col_map and col in ("STATUS_CODE", "STATUS", "STAT", "CODE"):
            # In 2026 CSV "CODE" is the status column; in older TXT it was the HCPCS column.
            # We only reach here if "code" was already mapped (HCPCS found first), so "CODE"
            # safely resolves to status.
            col_map["status"] = col
        elif "work_rvu" not in col_map and ("WORK_RVU" in col or col in ("WORK", "RVU")):
            # "RVU" (exact) is the work RVU column in the 2026 CSV; subsequent PE_RVU / RVU.1
            # columns won't match once work_rvu is already set.
            col_map["work_rvu"] = col

    # Fallback for pre-2026 TXT where HCPCS column was named "CODE"
    if "code" not in col_map and "CODE" in df.columns:
        col_map["code"] = "CODE"

    required = {"code", "description", "status", "work_rvu"}
    missing = required - set(col_map)
    if missing:
        raise ValueError(f"Missing expected columns in RVU file: {missing}. Available: {list(df.columns)}")

    records = []
    skipped = 0

    for _, row in df.iterrows():
        code = str(row[col_map["code"]]).strip()
        status = str(row[col_map["status"]]).strip().upper()

        # Only keep active, billable codes
        if status not in ACTIVE_STATUS_CODES:
            skipped += 1
            continue

        # Only numeric CPT codes (skip HCPCS alpha codes like G0001)
        if not code.isdigit():
            skipped += 1
            continue

        try:
            work_rvu = float(row[col_map["work_rvu"]])
        except (ValueError, TypeError):
            work_rvu = None

        code_int = int(code)
        category = get_category(code_int)
        body_system = get_body_system(code_int)
        is_surgical = category == "surgery" or category == "plastics"

        description = str(row[col_map["description"]]).strip()

        records.append({
            "code": code,
            "description": description,
            "category": category,
            "body_system": body_system,
            "avg_work_rvu": work_rvu if work_rvu and work_rvu > 0 else None,
            "is_surgical": is_surgical,
        })

    logger.info(f"Parsed {len(records)} active numeric CPT codes ({skipped} skipped)")
    return records


def run() -> int:
    """
    Download the CMS Physician Fee Schedule RVU file and load all active CPT codes.

    Returns:
        Number of CPT codes upserted.
    """
    zip_bytes = download_rvu_zip(CMS_RVU_URL)
    df = parse_rvu_zip(zip_bytes)
    records = extract_codes(df)

    # The 2026 CSV includes modifier rows that produce duplicate codes — keep first occurrence.
    seen: set[str] = set()
    records = [r for r in records if r["code"] not in seen and not seen.add(r["code"])]

    if not records:
        logger.warning("No CPT code records extracted — check RVU file format")
        return 0

    count = upsert_batch(
        table="cpt_codes",
        records=records,
        conflict_columns=["code"],
    )

    logger.info(f"Upserted {count} CPT codes")
    return count


if __name__ == "__main__":
    run()
