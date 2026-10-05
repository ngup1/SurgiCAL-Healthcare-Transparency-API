"""
CMS Hospital Quality Metrics Extractor

Loads hospital quality data from multiple CMS Hospital Compare datasets:
- Overall star ratings
- Patient Safety Indicators (PSI-90)
- Hospital-Acquired Conditions (HAC/HAI)
- Readmission and mortality rates
- Complication rates

Sources: https://data.cms.gov/provider-data/
Populates the `hospital_quality` table.
"""
from __future__ import annotations


from etl.common.config import get_logger
from etl.common.db import upsert_batch, execute_query
from etl.common.http import fetch_json, post_json

logger = get_logger(__name__)

# CMS Hospital Compare API endpoints (data.cms.gov datastore query API)
CMS_OVERALL_RATINGS_URL = (
    "https://data.cms.gov/provider-data/api/1/datastore/query/xubh-q36u/0"
)
CMS_COMPLICATIONS_URL = (
    "https://data.cms.gov/provider-data/api/1/datastore/query/ynj2-r877/0"
)
CMS_HAC_URL = (
    "https://data.cms.gov/provider-data/api/1/datastore/query/yq43-i98g/0"
)
CMS_READMISSIONS_URL = (
    "https://data.cms.gov/provider-data/api/1/datastore/query/9n3s-kdb3/0"
)

# Measure IDs we care about
MEASURE_MAP = {
    # PSI-90 composite
    "PSI_90_SAFETY": "psi90_composite",
    # Hip/knee complications and readmissions
    "COMP_HIP_KNEE": "complication_hip_knee",
    "READM_30_HIP_KNEE": "readmission_hip_knee",
    # CABG mortality and readmission
    "MORT_30_CABG": "mortality_cabg",
    "READM_30_CABG": "readmission_cabg",
    # Hospital-wide all-cause readmission (general quality signal for all procedures)
    "READM_30_HOSP_WIDE": "readmission_hosp_wide",
}

# HAI measure IDs -> JSON keys
HAI_MEASURES = {
    "HAI_1_SIR": "clabsi",
    "HAI_2_SIR": "cauti",
    "HAI_3_SIR": "ssi_colon",
    "HAI_4_SIR": "ssi_hysterectomy",
    "HAI_5_SIR": "mrsa",
    "HAI_6_SIR": "cdi",
}


def get_state_ccns(state: str = "CA") -> set[str]:
    """Get the set of hospital CCNs for the given state already in the database."""
    rows = execute_query("SELECT ccn FROM hospitals WHERE state = %s", (state,))
    return {r["ccn"] for r in rows}


def fetch_star_ratings(ccns: set[str]) -> dict[str, dict]:
    """
    Fetch overall hospital star ratings from the Hospital General Information dataset.

    Returns dict mapping CCN -> partial quality record.
    """
    logger.info("Fetching hospital star ratings...")
    results = {}

    # The star ratings are in the same Hospital General Information dataset
    # Field: hospital_overall_rating
    data = fetch_json(CMS_OVERALL_RATINGS_URL)
    for row in data.get("results", []):
        ccn = (row.get("facility_id") or "").strip()
        if ccn not in ccns:
            continue

        stars_raw = row.get("hospital_overall_rating", "")
        try:
            stars = float(stars_raw) if stars_raw and stars_raw != "Not Available" else None
        except ValueError:
            stars = None

        results[ccn] = {
            "ccn": ccn,
            "overall_stars": stars,
            "mortality_group": _parse_group(row.get("mortality_national_comparison")),
            "safety_group": _parse_group(row.get("safety_of_care_national_comparison")),
            "readmission_group": _parse_group(row.get("readmission_national_comparison")),
            "patient_experience_group": _parse_group(row.get("patient_experience_national_comparison")),
            "timely_care_group": _parse_group(row.get("timeliness_of_care_national_comparison")),
        }

    logger.info(f"Found star ratings for {len(results)} CA hospitals")
    return results


def _parse_group(value: str | None) -> str | None:
    """Parse CMS comparison text into standardized group label."""
    if not value:
        return None
    v = value.strip().lower()
    if "above" in v:
        return "above"
    elif "below" in v:
        return "below"
    elif "same" in v or "average" in v:
        return "same"
    return None


def _fetch_cms_quality_dataset(url: str, state: str, measure_filter: set[str]) -> list[dict]:
    """
    Paginate through a CMS quality dataset filtered by state, returning only
    rows whose measure_id is in measure_filter.
    """
    rows: list[dict] = []
    offset = 0
    page_size = 500

    while True:
        body = {
            "conditions": [
                {"property": "state", "value": state, "operator": "="}
            ],
            "limit": page_size,
            "offset": offset,
            "results": True,
            "schema": False,
            "keys": True,
            "rowIds": False,
        }
        data = post_json(url, body)
        page = data.get("results", [])
        for row in page:
            mid = (row.get("measure_id") or "").strip()
            if mid in measure_filter:
                rows.append(row)
        if len(page) < page_size:
            break
        offset += page_size

    return rows


def fetch_complications(ccns: set[str], state: str = "CA") -> dict[str, dict]:
    """Fetch complication measures (PSI-90, hip/knee complications)."""
    logger.info("Fetching complication measures...")
    results: dict[str, dict] = {}

    for row in _fetch_cms_quality_dataset(CMS_COMPLICATIONS_URL, state, set(MEASURE_MAP)):
        ccn = (row.get("facility_id") or row.get("provider_id") or "").strip()
        if ccn not in ccns:
            continue

        col = MEASURE_MAP[row["measure_id"].strip()]
        score_raw = row.get("score", "")
        try:
            score = float(score_raw) if score_raw and score_raw != "Not Available" else None
        except ValueError:
            score = None

        results.setdefault(ccn, {})[col] = score

    logger.info(f"Found complication data for {len(results)} hospitals")
    return results


def fetch_hai_rates(ccns: set[str], state: str = "CA") -> dict[str, dict]:
    """Fetch Hospital-Acquired Infection SIR rates."""
    logger.info("Fetching HAI rates...")
    results: dict[str, dict] = {}

    for row in _fetch_cms_quality_dataset(CMS_HAC_URL, state, set(HAI_MEASURES)):
        ccn = (row.get("facility_id") or row.get("provider_id") or "").strip()
        if ccn not in ccns:
            continue

        key = HAI_MEASURES[row["measure_id"].strip()]
        score_raw = row.get("score", "")
        try:
            score = float(score_raw) if score_raw and score_raw != "Not Available" else None
        except ValueError:
            score = None

        results.setdefault(ccn, {})[key] = score

    logger.info(f"Found HAI data for {len(results)} hospitals")
    return results


def fetch_readmissions(ccns: set[str], state: str = "CA") -> dict[str, dict]:
    """Fetch readmission and mortality measures."""
    logger.info("Fetching readmission/mortality measures...")
    results: dict[str, dict] = {}

    for row in _fetch_cms_quality_dataset(CMS_READMISSIONS_URL, state, set(MEASURE_MAP)):
        ccn = (row.get("facility_id") or row.get("provider_id") or "").strip()
        if ccn not in ccns:
            continue

        col = MEASURE_MAP[row["measure_id"].strip()]
        score_raw = row.get("score", "")
        try:
            score = float(score_raw) if score_raw and score_raw != "Not Available" else None
        except ValueError:
            score = None

        results.setdefault(ccn, {})[col] = score

    logger.info(f"Found readmission data for {len(results)} hospitals")
    return results


def run(state: str = "CA") -> int:
    """
    Run the hospital quality extractor.

    Merges data from multiple CMS datasets into hospital_quality rows.

    Returns:
        Number of quality records upserted.
    """
    logger.info("Starting CMS hospital quality extraction")

    ccns = get_state_ccns(state)
    if not ccns:
        logger.warning("No CA hospitals in database. Run cms_hospitals.py first.")
        return 0

    # Fetch all quality data sources
    star_data = fetch_star_ratings(ccns)
    complication_data = fetch_complications(ccns, state)
    hai_data = fetch_hai_rates(ccns, state)
    readmission_data = fetch_readmissions(ccns, state)

    # Merge all data by CCN
    merged: dict[str, dict] = {}
    for ccn in ccns:
        record = {"ccn": ccn}
        record.update(star_data.get(ccn, {}))
        record.update(complication_data.get(ccn, {}))
        record.update(readmission_data.get(ccn, {}))

        # HAI rates go into a JSONB column
        hai = hai_data.get(ccn, {})
        if hai:
            import json
            record["hai_sirs"] = json.dumps(hai)
        else:
            record["hai_sirs"] = None

        # Only include if we have any quality data
        has_data = any(
            v is not None
            for k, v in record.items()
            if k not in ("ccn", "hai_sirs")
        )
        if has_data or hai:
            merged[ccn] = record

    if not merged:
        logger.warning("No quality data found for CA hospitals")
        return 0

    records = list(merged.values())

    # Ensure all records have all columns (fill missing with None)
    all_keys = set()
    for r in records:
        all_keys.update(r.keys())
    for r in records:
        for k in all_keys:
            r.setdefault(k, None)

    count = upsert_batch(
        table="hospital_quality",
        records=records,
        conflict_columns=["ccn"],
    )

    logger.info(f"Loaded quality metrics for {count} hospitals")
    return count


if __name__ == "__main__":
    run()
