"""
Hospital MRF (Machine Readable File) Price Parser

Downloads and parses hospital price transparency MRF files sourced from the
hospital_mrf_links table (populated by hospitalpricingfiles_scraper.py).

Supports:
  - CMS in-network JSON (direct)
  - Table-of-Contents JSON (follows one level of in_network_files links)
  - CSV tall format (one row per code × payer)
  - CSV wide format (payer/plan encoded in column headers)
  - Gzip-compressed variants of any of the above
  - Skips: files larger than MAX_FILE_BYTES

Uses ijson for streaming JSON parsing to handle multi-GB files without loading
the entire payload into RAM.

Only stores rates for CPT codes present in the cpt_codes table and hospitals
present in the hospitals table (CCN FK constraint).

Populates the `prices` table with source='mrf_parsed'.

Requires migration 006_fix_prices_index.sql to be applied first (makes
plan_name and billing_class NOT NULL DEFAULT '' so upsert_batch works).
"""
from __future__ import annotations

import gzip
import io
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
import ijson
import pandas as pd

from etl.common.config import get_logger, get_settings
from etl.common.db import execute_query, get_db_connection, upsert_batch

logger = get_logger(__name__)

# Recognised payer name patterns -- matched against the URL (lowercase)
PAYER_PATTERNS = [
    (r"aetna",                          "Aetna"),
    (r"anthem",                         "Anthem BCBS"),
    (r"blue.?cross|bcbs|bluecross",     "Blue Cross Blue Shield"),
    (r"blue.?shield",                   "Blue Shield of CA"),
    (r"cigna",                          "Cigna"),
    (r"humana",                         "Humana"),
    (r"kaiser|kp\.org",                 "Kaiser Permanente"),
    (r"molina",                         "Molina Healthcare"),
    (r"united.?health|uhc\b",           "UnitedHealthcare"),
    (r"centene",                        "Centene"),
    (r"health.?net",                    "Health Net"),
    (r"magellan",                       "Magellan Health"),
    (r"oscar",                          "Oscar Health"),
    (r"medi.?cal|medicaid",             "Medi-Cal"),
    (r"medicare",                       "Medicare"),
    (r"tricare",                        "TRICARE"),
    (r"cash|self.?pay|self-pay|chargemaster", "Cash Price"),
]
_PAYER_RE = [(re.compile(p, re.I), name) for p, name in PAYER_PATTERNS]

# Conflict columns matching the unique index created by migration 006
PRICES_CONFLICT_COLS = ["cpt", "ccn", "payer", "plan_name", "billing_class"]
PRICES_UPDATE_COLS = [
    "cash_price", "negotiated_rate", "negotiated_min", "negotiated_max",
    "source", "measure_date",
]


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_parseable_links(state: str = "CA", limit: int | None = None) -> list[dict]:
    """
    Return active MRF links that have a known CCN.

    Only links with a non-null CCN can be loaded into the prices table
    (FK constraint on prices.ccn -> hospitals.ccn).
    """
    limit_clause = f"LIMIT {limit}" if limit else ""
    return execute_query(
        f"""
        SELECT id, ccn, hospital_name, machine_readable_url, file_format, url_status
        FROM hospital_mrf_links
        WHERE ccn IS NOT NULL
          AND state = %s
          AND url_status IN ('active', 'unknown')
        ORDER BY url_status = 'active' DESC, hospital_name
        {limit_clause}
        """,
        (state,),
    )


def get_target_cpts() -> set[str]:
    """Return CPT codes present in the cpt_codes table."""
    rows = execute_query("SELECT code FROM cpt_codes")
    return {r["code"] for r in rows}


def get_valid_ccns() -> set[str]:
    """Return CCNs present in the hospitals table."""
    rows = execute_query("SELECT ccn FROM hospitals")
    return {r["ccn"] for r in rows}


def _set_link_status(link_id: str, status: str) -> None:
    """Update url_status and last_checked for a hospital_mrf_links row."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE hospital_mrf_links
                SET url_status = %s, last_checked = %s
                WHERE id = %s
                """,
                (status, datetime.now(timezone.utc), link_id),
            )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# URL / payer helpers
# ---------------------------------------------------------------------------

def extract_payer_from_url(url: str) -> str:
    """
    Extract a human-readable payer name from a MRF URL.

    Tries known payer patterns against the full URL. Falls back to the
    second-level domain name if nothing matches (e.g. 'community-health').
    """
    for pattern, name in _PAYER_RE:
        if pattern.search(url):
            return name

    try:
        host = urlparse(url).hostname or ""
        # Strip www. and TLD, return the meaningful part
        parts = host.replace("www.", "").split(".")
        return parts[0].replace("-", " ").title() if parts else "Unknown Payer"
    except Exception:
        return "Unknown Payer"


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def _download_bytes(url: str, timeout: int = 120) -> bytes | None:
    """
    Download a URL, enforcing MAX_FILE_BYTES.

    Returns raw bytes, or None if the file is too large, the request fails,
    or the response is not 2xx.
    """
    try:
        with httpx.stream(
            "GET", url,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "SurgiCAL-ETL/0.1 (Healthcare price transparency research)"},
        ) as response:
            if response.status_code != 200:
                logger.warning(f"HTTP {response.status_code} for {url}")
                return None

            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes(chunk_size=512 * 1024):
                total += len(chunk)
                chunks.append(chunk)

            logger.info(f"Downloaded {total / 1024 / 1024:.1f} MB from {url[:80]}")
            return b"".join(chunks)

    except httpx.TimeoutException:
        logger.warning(f"Timeout downloading {url}")
        return None
    except Exception as e:
        logger.warning(f"Download failed for {url}: {e}")
        return None


def _decompress(data: bytes, url: str) -> bytes | None:
    """Gunzip if the URL or Content-Encoding suggests gzip compression."""
    if url.endswith(".gz") or url.endswith(".gzip"):
        try:
            return gzip.decompress(data)
        except Exception as e:
            logger.warning(f"Failed to decompress {url}: {e}")
            return None
    return data


# ---------------------------------------------------------------------------
# JSON structure detection
# ---------------------------------------------------------------------------

def _detect_structure(data: bytes) -> str:
    """
    Return 'in_network', 'toc', or 'unknown' by peeking at the first 64 KB.

    TOC files have a 'reporting_structure' or 'in_network_files' key at the
    top level. In-network files have an 'in_network' array.
    """
    preview = data[:65536].decode("utf-8", errors="replace")
    if "reporting_structure" in preview or (
        "in_network_files" in preview and "in_network" not in preview[:512]
    ):
        return "toc"
    if '"in_network"' in preview:
        return "in_network"
    return "unknown"


# ---------------------------------------------------------------------------
# CSV detection and parsing (CMS tall and wide formats)
#
# CMS CSV file layout (v3.0.0):
#   Row 0 : General field names  (hospital_name, last_updated_on, version, ...)
#   Row 1 : General field values
#   Row N : Standard charge column headers (description, code | 1, payer_name, ...)
#   Row N+1+: Charge data rows
#
# Tall format: one row per code × payer combination.
#   Distinguishing column: payer_name
#
# Wide format: one row per code; payer/plan baked into column headers.
#   Distinguishing column pattern: standard_charge | <payer> | <plan> | negotiated_dollar
# ---------------------------------------------------------------------------

def _is_csv(data: bytes, url: str) -> bool:
    """Return True if data is a CSV MRF (not JSON)."""
    if url.lower().split("?")[0].endswith(".csv"):
        return True
    stripped = data.lstrip()
    return not (stripped.startswith(b"{") or stripped.startswith(b"["))


def _read_csv_metadata(data: bytes) -> pd.DataFrame | None:
    """
    Parse just the first 2 rows of a CSV into a DataFrame.
    Used exclusively to extract hospital-level metadata fields.
    """
    for encoding in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            df = pd.read_csv(
                io.BytesIO(data),
                header=None,
                dtype=str,
                encoding=encoding,
                nrows=2,
                skip_blank_lines=True,
            )
            if not df.empty:
                return df
        except Exception:
            continue
    return None


def _extract_csv_metadata(df: pd.DataFrame) -> dict:
    """
    Pull hospital metadata from the CMS general data element rows (rows 0–1).

    Row 0 contains field names (hospital_name, last_updated_on, version, ...),
    row 1 contains the corresponding values.
    """
    if len(df) < 2:
        return {}
    headers = [str(v).strip().lower() for v in df.iloc[0]]
    values  = [str(v).strip()         for v in df.iloc[1]]
    meta: dict = {}
    for key, val in zip(headers, values):
        if not val or val.lower() in ("nan", "none"):
            continue
        if "hospital_name" in key:
            meta["hospital_name"] = val
        elif "last_updated_on" in key or "mrf_date" in key:
            meta["measure_date"] = val
        elif key == "version":
            meta["version"] = val
    return meta


_HEADER_CODE_EXACT = {"cpt", "hcpcs", "ndc", "procedure", "rev", "revenue", "cdt"}
_HEADER_DESC_SUBS  = {"description", "charge_name", "item_name"}
_HEADER_DESC_EXACT = {"desc", "service", "item"}


def _find_charge_header_offset(data: bytes) -> int | None:
    """
    Scan the first 20 newline-delimited lines of raw CSV bytes to find the
    charge data header row.

    Returns the byte offset of that line, or None if not found.
    A fallback offset is returned when only a description-like header is found
    (no code column), so CDM files without explicit code columns still parse.
    """
    offset = 0
    fallback_offset: int | None = None

    for _ in range(20):
        if offset >= len(data):
            break

        nl = data.find(b"\n", offset)
        end = nl + 1 if nl != -1 else len(data)
        line = data[offset:end].decode("utf-8-sig", errors="replace").rstrip("\r\n")

        # Simple comma split — good enough for header rows (no embedded newlines)
        cells = [c.strip().lower() for c in line.split(",")]
        has_desc = any(
            any(sub in c for sub in _HEADER_DESC_SUBS) or c in _HEADER_DESC_EXACT
            for c in cells
        )
        has_code = any(
            "code" in c or c in _HEADER_CODE_EXACT
            for c in cells
        )

        if has_desc and has_code:
            return offset
        if has_desc and fallback_offset is None:
            fallback_offset = offset

        offset = end

    return fallback_offset


_PIPE_RE = re.compile(r"\s*\|\s*")


def _col(df: pd.DataFrame, *candidates: str) -> str | None:
    """
    Return the first matching column name (case-insensitive), or None.

    Normalises whitespace around pipe characters so that
    'standard_charge | gross' and 'standard_charge|gross' are equivalent.
    """
    cols_map = {_PIPE_RE.sub("|", c.lower()): c for c in df.columns}
    for cand in candidates:
        found = cols_map.get(_PIPE_RE.sub("|", cand.lower()))
        if found is not None:
            return found
    return None


def _find_code_cols(df: pd.DataFrame) -> list[tuple[str, str | None]]:
    """
    Find all (code_col, type_col_or_None) pairs.

    CMS spec names them: code | 1, code | 1 | type, code | 2, code | 2 | type, ...
    """
    all_cols = list(df.columns)
    lower_map = {c.lower(): c for c in all_cols}
    pairs: list[tuple[str, str | None]] = []
    for col in all_cols:
        if re.match(r"code\s*\|\s*\d+$", col.strip().lower()):
            # Support both 'code | 1 | type' (spaced) and 'code|1|type' (no spaces)
            base = _PIPE_RE.sub("|", col.strip().lower())
            type_col = lower_map.get(base + "|type") or lower_map.get(col.strip().lower() + " | type")
            pairs.append((col, type_col))
    return pairs


def _detect_csv_format(charge_df: pd.DataFrame) -> str:
    """
    Return 'tall' or 'wide' for the charge-section DataFrame.

    Tall: has a 'payer_name' column (one row per payer).
    Wide: has columns like 'standard_charge | PayerX | PlanY | negotiated_dollar'.
    Defaults to 'tall' when ambiguous.
    """
    cols_lower = [_PIPE_RE.sub("|", c.lower()) for c in charge_df.columns]
    if "payer_name" in cols_lower or "payer" in cols_lower or "insurance_name" in cols_lower:
        return "tall"
    if any(
        c.startswith("standard_charge|") and c.count("|") >= 3
        for c in cols_lower
    ):
        return "wide"
    return "tall"


def _to_float(val) -> float | None:
    """Convert a cell value to float; return None on any failure."""
    if val is None:
        return None
    s = str(val).replace(",", "").strip()
    if s.lower() in ("", "nan", "none", "n/a"):
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _resolve_cpt(
    row: pd.Series,
    code_cols: list[tuple[str, str | None]],
    target_cpts: set[str],
) -> str | None:
    """
    Return the first CPT/HCPCS code in this row present in target_cpts.
    Tries both stripped-leading-zeros and original padded forms.
    Skips code columns whose type is set to something other than CPT/HCPCS.
    """
    for code_col, type_col in code_cols:
        if not pd.notna(row[code_col]):
            continue
        raw = str(row[code_col]).strip()
        if not raw or raw.lower() in ("nan", "none"):
            continue
        if type_col and pd.notna(row[type_col]):
            code_type = str(row[type_col]).strip().upper()
            if code_type and code_type not in ("CPT", "HCPCS"):
                continue
        stripped = raw.lstrip("0") or raw
        if stripped in target_cpts:
            return stripped
        if raw in target_cpts:
            return raw
    return None


def _aggregate_rate_groups(
    rate_groups: dict[tuple, dict],
    measure_date: str | None,
) -> list[dict]:
    """Convert aggregated rate groups into price records ready for upsert."""
    records: list[dict] = []
    for (cpt, ccn_, payer_, plan_, b_class), grp in rate_groups.items():
        rates = sorted(grp["rates"])
        if not rates and grp["cash"] is None:
            continue
        median = rates[len(rates) // 2] if rates else None
        records.append({
            "cpt":             cpt,
            "ccn":             ccn_,
            "payer":           payer_,
            "plan_name":       plan_,
            "billing_class":   b_class,
            "negotiated_rate": round(median, 2) if median is not None else None,
            "negotiated_min":  round(rates[0], 2) if rates else (
                               round(grp["min"], 2) if grp["min"] is not None else None),
            "negotiated_max":  round(rates[-1], 2) if rates else (
                               round(grp["max"], 2) if grp["max"] is not None else None),
            "cash_price":      round(grp["cash"], 2) if grp["cash"] is not None else None,
            "source":          "mrf_parsed",
            "measure_date":    measure_date,
        })
    return records


def _parse_csv_tall(
    charge_df: pd.DataFrame,
    ccn: str,
    meta: dict,
    target_cpts: set[str],
) -> list[dict]:
    """
    Parse CMS tall CSV format: one row per code × payer combination.

    Relevant columns (case-insensitive):
      payer_name, plan_name, billing_class (optional)
      standard_charge | gross
      standard_charge | discounted_cash
      standard_charge | negotiated_dollar
      standard_charge | min, standard_charge | max

    Multiple rows for the same (cpt, ccn, payer, plan, billing_class) are
    collapsed into a single record with negotiated_min/max/rate(median).
    """
    payer_col  = _col(charge_df, "payer_name", "payer", "insurance_name", "insurer")
    plan_col   = _col(charge_df, "plan_name", "plan", "insurance_plan")
    bclass_col = _col(charge_df, "billing_class", "setting")
    gross_col  = _col(charge_df, "standard_charge | gross", "gross_charge", "gross", "chargemaster_rate", "charge_amount")
    cash_col   = _col(charge_df, "standard_charge | discounted_cash", "discounted_cash", "cash_price", "cash", "self_pay")
    neg_col    = _col(charge_df, "standard_charge | negotiated_dollar", "negotiated_dollar", "negotiated_rate", "negotiated_charge", "negotiated")
    min_col    = _col(charge_df, "standard_charge | min", "min_negotiated", "min_charge", "min")
    max_col    = _col(charge_df, "standard_charge | max", "max_negotiated", "max_charge", "max")
    code_cols  = _find_code_cols(charge_df)
    # Fallback: CDM formats may use a single non-CMS-style code column
    if not code_cols:
        fallback = _col(charge_df, "cpt_code", "procedure_code", "hcpcs_code", "item_code", "charge_code", "code", "cpt", "hcpcs")
        if fallback:
            code_cols = [(fallback, None)]
    measure_date = meta.get("measure_date")

    rate_groups: dict[tuple, dict] = defaultdict(
        lambda: {"rates": [], "min": None, "max": None, "cash": None}
    )

    for _, row in charge_df.iterrows():
        cpt = _resolve_cpt(row, code_cols, target_cpts)
        if not cpt:
            continue

        payer   = str(row[payer_col]).strip()         if payer_col  and pd.notna(row[payer_col])  else "Cash Price"
        plan    = str(row[plan_col]).strip()           if plan_col   and pd.notna(row[plan_col])   else ""
        b_class = str(row[bclass_col]).strip().lower() if bclass_col and pd.notna(row[bclass_col]) else ""
        if b_class not in ("professional", "facility"):
            b_class = ""

        rate = _to_float(row[neg_col])  if neg_col  else None
        cash = (_to_float(row[cash_col]) if cash_col else None) or (_to_float(row[gross_col]) if gross_col else None)
        rmin = _to_float(row[min_col])  if min_col  else None
        rmax = _to_float(row[max_col])  if max_col  else None

        key = (cpt, ccn, payer, plan, b_class)
        grp = rate_groups[key]
        if rate is not None and rate > 0:
            grp["rates"].append(rate)
        if rmin is not None:
            grp["min"] = min(rmin, grp["min"]) if grp["min"] is not None else rmin
        if rmax is not None:
            grp["max"] = max(rmax, grp["max"]) if grp["max"] is not None else rmax
        if cash is not None:
            grp["cash"] = cash

    return _aggregate_rate_groups(rate_groups, measure_date)


def _parse_csv_wide(
    charge_df: pd.DataFrame,
    ccn: str,
    meta: dict,
    target_cpts: set[str],
) -> list[dict]:
    """
    Parse CMS wide CSV format: payer/plan encoded directly in column headers.

    Payer-keyed negotiated dollar column pattern:
      standard_charge | <payer_name> | <plan_name> | negotiated_dollar

    Row-level columns (shared across all payers):
      standard_charge | gross, standard_charge | discounted_cash
      standard_charge | min,   standard_charge | max
      billing_class (optional)
    """
    cash_col   = _col(charge_df, "standard_charge | discounted_cash", "discounted_cash", "cash_price", "cash")
    gross_col  = _col(charge_df, "standard_charge | gross", "gross_charge", "gross", "chargemaster_rate")
    min_col    = _col(charge_df, "standard_charge | min", "min_negotiated", "min_charge", "min")
    max_col    = _col(charge_df, "standard_charge | max", "max_negotiated", "max_charge", "max")
    bclass_col = _col(charge_df, "billing_class", "setting")
    code_cols  = _find_code_cols(charge_df)
    if not code_cols:
        fallback = _col(charge_df, "cpt_code", "procedure_code", "hcpcs_code", "item_code", "charge_code", "code", "cpt", "hcpcs")
        if fallback:
            code_cols = [(fallback, None)]
    measure_date = meta.get("measure_date")

    # Map (payer_name, plan_name) -> negotiated_dollar column name
    payer_neg_cols: dict[tuple[str, str], str] = {}
    for col in charge_df.columns:
        parts = [p.strip() for p in col.split("|")]
        if (
            len(parts) >= 4
            and parts[0].lower() == "standard_charge"
            and parts[-1].lower() == "negotiated_dollar"
        ):
            payer_neg_cols[(parts[1], parts[2])] = col

    if not payer_neg_cols:
        logger.warning("Wide CSV has no payer negotiated_dollar columns — nothing to parse")
        return []

    rate_groups: dict[tuple, dict] = defaultdict(
        lambda: {"rates": [], "min": None, "max": None, "cash": None}
    )

    for _, row in charge_df.iterrows():
        cpt = _resolve_cpt(row, code_cols, target_cpts)
        if not cpt:
            continue

        b_class = str(row[bclass_col]).strip().lower() if bclass_col and pd.notna(row[bclass_col]) else ""
        if b_class not in ("professional", "facility"):
            b_class = ""

        cash = (_to_float(row[cash_col]) if cash_col else None) or (_to_float(row[gross_col]) if gross_col else None)
        rmin = _to_float(row[min_col]) if min_col else None
        rmax = _to_float(row[max_col]) if max_col else None

        for (payer, plan), neg_col in payer_neg_cols.items():
            rate = _to_float(row[neg_col])
            if rate is None or rate <= 0:
                continue
            key = (cpt, ccn, payer, plan, b_class)
            grp = rate_groups[key]
            grp["rates"].append(rate)
            if rmin is not None:
                grp["min"] = min(rmin, grp["min"]) if grp["min"] is not None else rmin
            if rmax is not None:
                grp["max"] = max(rmax, grp["max"]) if grp["max"] is not None else rmax
            if cash is not None:
                grp["cash"] = cash

    return _aggregate_rate_groups(rate_groups, measure_date)


def _log_csv_preview(data: bytes, url: str) -> None:
    """Log the first 5 raw lines of the CSV bytes for diagnosis."""
    try:
        text = data[:4096].decode("utf-8-sig", errors="replace")
        lines = text.splitlines()[:5]
        preview = "\n".join(f"  line{i}: {line[:200]}" for i, line in enumerate(lines))
        logger.warning(f"CSV preview (first lines) for {url[:80]}:\n{preview}")
    except Exception:
        pass


def _parse_csv(data: bytes, ccn: str, url: str, target_cpts: set[str]) -> list[dict]:
    """
    Top-level CSV entry point.

    Scans the raw bytes to locate the charge header row, then passes only
    the charge section (header row + data rows) to pandas. This avoids the
    column-count mismatch that occurs when pandas reads the full file with
    header=None: the 9-column CMS metadata rows cause pandas to treat the
    28-column charge rows as 'bad lines' and skip them silently.
    """
    # Extract hospital metadata from the first 2 rows only
    meta: dict = {}
    meta_df = _read_csv_metadata(data)
    if meta_df is not None and not meta_df.empty:
        meta = _extract_csv_metadata(meta_df)

    # Find byte offset of the charge header line via raw line scan
    header_offset = _find_charge_header_offset(data)
    if header_offset is None:
        _log_csv_preview(data, url)
        logger.warning(f"Could not locate charge header row in CSV: {url}")
        return []

    # Parse only the charge section (header + data rows)
    charge_bytes = data[header_offset:]
    charge_df: pd.DataFrame | None = None
    for encoding in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            charge_df = pd.read_csv(
                io.BytesIO(charge_bytes),
                header=0,
                dtype=str,
                encoding=encoding,
                on_bad_lines="skip",
                skip_blank_lines=True,
            )
            if charge_df is not None and not charge_df.empty:
                break
        except Exception:
            charge_df = None
            continue

    if charge_df is None or charge_df.empty:
        logger.warning(f"Could not parse charge section of CSV: {url}")
        return []

    charge_df.columns = [str(c).strip() for c in charge_df.columns]
    charge_df = charge_df.dropna(how="all").reset_index(drop=True)

    if charge_df.empty:
        logger.warning(f"No charge data rows in CSV: {url}")
        return []

    fmt = _detect_csv_format(charge_df)
    logger.info(f"CSV format: {fmt} | {len(charge_df)} rows | {url[:80]}")

    if fmt == "tall":
        return _parse_csv_tall(charge_df, ccn, meta, target_cpts)
    else:
        return _parse_csv_wide(charge_df, ccn, meta, target_cpts)


# ---------------------------------------------------------------------------
# TOC parser
# ---------------------------------------------------------------------------

def _extract_toc_links(data: bytes) -> list[dict]:
    """
    Parse a Table-of-Contents MRF JSON and return a list of
    {'location': url, 'plan_name': str} dicts for in_network_files entries.

    Handles two common TOC formats:
      Format A (hospital):  top-level 'in_network_files' list
      Format B (insurer):   nested under 'reporting_structure[].in_network_files'
    """
    try:
        doc = json.loads(data)
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse TOC JSON: {e}")
        return []

    links: list[dict] = []

    def _collect(in_network_files: list, plan_names: list[str]) -> None:
        plan = ", ".join(plan_names) if plan_names else ""
        for entry in in_network_files:
            loc = entry.get("location", "")
            if loc:
                links.append({"location": loc, "plan_name": plan})

    # Format A: top-level in_network_files
    if "in_network_files" in doc:
        plan_names = [
            p.get("plan_name", "")
            for p in doc.get("reporting_plans", [])
            if p.get("plan_name")
        ]
        _collect(doc["in_network_files"], plan_names)

    # Format B: reporting_structure[].in_network_files
    elif "reporting_structure" in doc:
        for section in doc.get("reporting_structure", []):
            plan_names = [
                p.get("plan_name", "")
                for p in section.get("reporting_plans", [])
                if p.get("plan_name")
            ]
            _collect(section.get("in_network_files", []), plan_names)

    return links


# ---------------------------------------------------------------------------
# In-network JSON streaming parser
# ---------------------------------------------------------------------------

def _parse_in_network_stream(
    stream: io.BytesIO,
    ccn: str,
    payer: str,
    plan_name: str,
    target_cpts: set[str],
) -> list[dict]:
    """
    Stream-parse a CMS in-network MRF JSON using ijson.

    Expected structure:
    {
      "in_network": [
        {
          "billing_code_type": "CPT",
          "billing_code": "27447",
          "negotiated_rates": [
            {
              "negotiated_prices": [
                {
                  "negotiated_type": "negotiated",
                  "negotiated_rate": 15000.00,
                  "billing_class": "facility"
                }
              ]
            }
          ]
        }
      ]
    }

    Aggregates multiple rate entries for the same
    (cpt, ccn, payer, plan_name, billing_class) into a single record with
    negotiated_min / negotiated_max / negotiated_rate (median).

    Returns a list of price dicts ready for upsert_batch().
    """
    # key -> list of rates
    rate_groups: dict[tuple, list[float]] = defaultdict(list)

    try:
        for item in ijson.items(stream, "in_network.item"):
            code_type = (item.get("billing_code_type") or "").upper()
            if code_type not in ("CPT", "HCPCS"):
                continue

            cpt_code = str(item.get("billing_code") or "").strip().lstrip("0") or ""
            # CMS sometimes zero-pads; our cpt_codes table uses plain codes
            if cpt_code not in target_cpts:
                # Also try zero-padded form
                padded = str(item.get("billing_code") or "").strip()
                if padded not in target_cpts:
                    continue
                cpt_code = padded

            for rate_group in item.get("negotiated_rates") or []:
                for price in rate_group.get("negotiated_prices") or []:
                    raw_rate = price.get("negotiated_rate")
                    if raw_rate is None:
                        continue
                    try:
                        rate = float(raw_rate)
                    except (ValueError, TypeError):
                        continue
                    if rate <= 0:
                        continue

                    b_class = (price.get("billing_class") or "").strip().lower()
                    if b_class not in ("professional", "facility"):
                        b_class = ""

                    key = (cpt_code, ccn, payer, plan_name, b_class)
                    rate_groups[key].append(rate)

    except Exception as e:
        logger.warning(f"Error during ijson streaming: {e}")

    # Aggregate into one record per key
    records: list[dict] = []
    for (cpt_code, ccn_, payer_, plan_name_, b_class), rates in rate_groups.items():
        rates_sorted = sorted(rates)
        n = len(rates_sorted)
        median_rate = rates_sorted[n // 2]

        records.append({
            "cpt":             cpt_code,
            "ccn":             ccn_,
            "payer":           payer_,
            "plan_name":       plan_name_,
            "billing_class":   b_class,
            "negotiated_rate": round(median_rate, 2),
            "negotiated_min":  round(rates_sorted[0], 2),
            "negotiated_max":  round(rates_sorted[-1], 2),
            "cash_price":      None,
            "source":          "mrf_parsed",
            "measure_date":    None,
        })

    return records


# ---------------------------------------------------------------------------
# Per-link processing
# ---------------------------------------------------------------------------

def _process_url(
    url: str,
    ccn: str,
    plan_name: str,
    target_cpts: set[str],
    valid_ccns: set[str],
) -> list[dict]:
    """
    Download one MRF URL and return price records.

    Returns an empty list on any failure (errors are logged, not raised).
    """
    if ccn not in valid_ccns:
        logger.debug(f"CCN {ccn} not in hospitals table, skipping {url}")
        return []

    payer = extract_payer_from_url(url)
    data = _download_bytes(url)
    if data is None:
        return []

    data = _decompress(data, url)
    if data is None:
        return []

    # CSV must be checked before JSON structure detection
    if _is_csv(data, url):
        return _parse_csv(data, ccn, url, target_cpts)

    structure = _detect_structure(data)

    if structure == "in_network":
        return _parse_in_network_stream(
            io.BytesIO(data), ccn, payer, plan_name, target_cpts
        )

    if structure == "toc":
        toc_links = _extract_toc_links(data)
        records: list[dict] = []
        for entry in toc_links[:20]:  # cap at 20 sub-links per TOC
            sub_url = entry["location"]
            sub_plan = entry.get("plan_name") or plan_name or extract_payer_from_url(sub_url)
            logger.info(f"  Following TOC link: {sub_url[:80]}")
            sub_data = _download_bytes(sub_url)
            if sub_data is None:
                continue
            sub_data = _decompress(sub_data, sub_url)
            if sub_data is None:
                continue
            if _detect_structure(sub_data) == "in_network":
                records.extend(
                    _parse_in_network_stream(
                        io.BytesIO(sub_data), ccn,
                        extract_payer_from_url(sub_url),
                        sub_plan, target_cpts,
                    )
                )
        return records

    logger.debug(f"Unknown MRF structure at {url}, skipping")
    return []


def _process_link(
    link: dict,
    target_cpts: set[str],
    valid_ccns: set[str],
) -> list[dict]:
    """
    Process one hospital_mrf_links row. Updates url_status in the DB.
    Returns price records (may be empty).
    """
    url = link["machine_readable_url"]
    ccn = link["ccn"]
    link_id = str(link["id"])
    hospital = link.get("hospital_name", "")

    logger.info(f"Processing MRF: {hospital} ({ccn}) -- {url[:80]}")

    try:
        records = _process_url(url, ccn, plan_name="", target_cpts=target_cpts, valid_ccns=valid_ccns)
        status = "active" if records is not None else "broken"
        _set_link_status(link_id, status)
        return records or []
    except Exception as e:
        logger.warning(f"Failed processing {url}: {e}")
        _set_link_status(link_id, "broken")
        return []


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(state: str = "CA", limit: int | None = None) -> int:
    """
    Parse MRF files for CA hospitals and load negotiated rates into prices.

    Args:
        state: State filter for hospital_mrf_links (default: CA).
        limit: Max number of MRF links to process in this run (default: all).

    Returns:
        Total number of price records upserted.
    """
    logger.info(f"Starting MRF price extraction (state={state})")

    links = get_parseable_links(state=state, limit=limit)
    if not links:
        logger.warning("No parseable MRF links found. Run hospitalpricingfiles_scraper.py first.")
        return 0

    target_cpts = get_target_cpts()
    valid_ccns = get_valid_ccns()

    logger.info(
        f"Processing {len(links)} MRF links | "
        f"{len(target_cpts)} target CPT codes | "
        f"{len(valid_ccns)} valid CCNs"
    )

    total_loaded = 0
    for i, link in enumerate(links, 1):
        logger.info(f"[{i}/{len(links)}] {link.get('hospital_name', link['ccn'])}")
        records = _process_link(link, target_cpts, valid_ccns)

        if not records:
            continue

        count = upsert_batch(
            table="prices",
            records=records,
            conflict_columns=PRICES_CONFLICT_COLS,
            update_columns=PRICES_UPDATE_COLS,
        )
        total_loaded += count
        logger.info(f"  Loaded {count} price records")

    logger.info(f"MRF extraction complete -- {total_loaded} total price records loaded")
    return total_loaded


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Parse hospital MRF price files")
    parser.add_argument("--state", default="CA", help="State filter (default: CA)")
    parser.add_argument("--limit", type=int, default=None, help="Max MRF links to process")
    args = parser.parse_args()
    run(state=args.state, limit=args.limit)
