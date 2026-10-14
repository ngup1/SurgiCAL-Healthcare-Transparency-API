"""
CMS Hospital Quality Metrics Extractor

Loads hospital quality measures from three CMS Hospital Compare datasets:
- Complications and Deaths (ynj2-r877): PSI-90, hip/knee complications, CABG mortality
- Healthcare Associated Infections (77hc-ibv8): the six infection ratios (SIRs)
- Unplanned Hospital Visits (632h-zaca): readmission rates

Star ratings and the mortality/safety/readmission comparison groups are in the
Hospital General Information dataset and are loaded by cms_hospitals.py.

Sources: https://data.cms.gov/provider-data/
Populates the measure columns of the `hospital_quality` table.
"""
from __future__ import annotations

import json
from datetime import datetime

from etl.common.config import get_logger
from etl.common.db import execute_query, upsert_batch
from etl.common.http import RateLimitedClient

logger = get_logger(__name__)

CMS_DATASTORE_URL = "https://data.cms.gov/provider-data/api/1/datastore/query/{dataset}/0"

# Dataset ID -> {CMS measure_id: hospital_quality column}
DATASET_MEASURES = {
    # Complications and Deaths
    "ynj2-r877": {
        "PSI_90": "psi90_composite",
        "COMP_HIP_KNEE": "complication_hip_knee",
        "MORT_30_CABG": "mortality_cabg",
    },
    # Unplanned Hospital Visits
    "632h-zaca": {
        "READM_30_HIP_KNEE": "readmission_hip_knee",
        "READM_30_CABG": "readmission_cabg",
        # Hospital-wide all-cause readmission (general quality signal for all procedures)
        "Hybrid_HWR": "readmission_hosp_wide",
    },
}

# Healthcare Associated Infections: measure IDs -> keys of the hai_sirs JSON column
HAI_DATASET = "77hc-ibv8"
HAI_MEASURES = {
    "HAI_1_SIR": "clabsi",
    "HAI_2_SIR": "cauti",
    "HAI_3_SIR": "ssi_colon",
    "HAI_4_SIR": "ssi_hysterectomy",
    "HAI_5_SIR": "mrsa",
    "HAI_6_SIR": "cdi",
}

MEASURE_COLUMNS = [col for measures in DATASET_MEASURES.values() for col in measures.values()]


def get_state_ccns(state: str = "CA") -> set[str]:
    """Get the set of hospital CCNs for the given state already in the database."""
    rows = execute_query("SELECT ccn FROM hospitals WHERE state = %s", (state,))
    return {r["ccn"] for r in rows}


def fetch_measures(client: RateLimitedClient, dataset: str, state: str, measure_ids: list[str]) -> list[dict]:
    """
    Page through one CMS quality dataset, filtered on the server to one state
    and the given measure IDs.
    """
    rows: list[dict] = []
    offset = 0
    page_size = 500

    while True:
        body = {
            "conditions": [
                {"property": "state", "value": state, "operator": "="},
                {"property": "measure_id", "value": measure_ids, "operator": "in"},
            ],
            "limit": page_size,
            "offset": offset,
            "sort": {"property": "facility_id", "order": "asc"},
            "results": True,
            "schema": False,
            "keys": True,
            "rowIds": False,
        }
        page = client.post_json(CMS_DATASTORE_URL.format(dataset=dataset), body)["results"]
        rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size

    return rows


def parse_score(raw) -> float | None:
    """A measure score, or None for 'Not Available' and other non-numbers."""
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def quarter_of(end_date: str | None) -> str | None:
    """'09/30/2025' -> '2025Q3', the form hospital_quality.measure_period uses."""
    try:
        d = datetime.strptime((end_date or "").strip(), "%m/%d/%Y")
    except ValueError:
        return None
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


def merge_quality(
    ccns: set[str],
    measure_rows: dict[str, list[dict]],
    hai_rows: list[dict],
) -> list[dict]:
    """
    Combine rows from the measure datasets into one hospital_quality record per
    hospital that has at least one score.

    measure_rows maps a dataset ID to that dataset's rows.
    """
    merged: dict[str, dict] = {}
    periods: dict[str, str] = {}

    def record_for(row: dict) -> dict | None:
        ccn = (row.get("facility_id") or "").strip()
        if ccn not in ccns:
            return None
        period = quarter_of(row.get("end_date"))
        if period and period > periods.get(ccn, ""):
            periods[ccn] = period
        return merged.setdefault(ccn, {"ccn": ccn, **dict.fromkeys(MEASURE_COLUMNS), "hai_sirs": {}})

    for dataset, rows in measure_rows.items():
        columns = DATASET_MEASURES[dataset]
        for row in rows:
            column = columns.get((row.get("measure_id") or "").strip())
            record = record_for(row) if column else None
            if record is not None:
                record[column] = parse_score(row.get("score"))

    for row in hai_rows:
        key = HAI_MEASURES.get((row.get("measure_id") or "").strip())
        score = parse_score(row.get("score"))
        record = record_for(row) if key else None
        if record is not None and score is not None:
            record["hai_sirs"][key] = score

    records = []
    for ccn, record in merged.items():
        hai = record["hai_sirs"]
        if not hai and all(record[c] is None for c in MEASURE_COLUMNS):
            continue
        record["hai_sirs"] = json.dumps(hai) if hai else None
        record["measure_period"] = periods.get(ccn)
        records.append(record)
    return records


def run(state: str = "CA") -> int:
    """
    Run the hospital quality extractor.

    Merges data from the CMS measure datasets into hospital_quality rows.

    Returns:
        Number of quality records upserted.
    """
    logger.info("Starting CMS hospital quality extraction")

    ccns = get_state_ccns(state)
    if not ccns:
        logger.warning(f"No {state} hospitals in database. Run cms_hospitals.py first.")
        return 0

    with RateLimitedClient() as client:
        measure_rows = {}
        for dataset, measures in DATASET_MEASURES.items():
            measure_rows[dataset] = fetch_measures(client, dataset, state, list(measures))
            logger.info(f"Fetched {len(measure_rows[dataset])} rows from {dataset}")
            if not measure_rows[dataset]:
                # Every hospital has a row per measure, scored or not, so none means the
                # dataset or its measure IDs changed.
                raise RuntimeError(f"CMS dataset {dataset} returned no rows for {sorted(measures)}")
        hai_rows = fetch_measures(client, HAI_DATASET, state, list(HAI_MEASURES))
        logger.info(f"Fetched {len(hai_rows)} rows from {HAI_DATASET}")
        if not hai_rows:
            raise RuntimeError(f"CMS dataset {HAI_DATASET} returned no rows for {sorted(HAI_MEASURES)}")

    records = merge_quality(ccns, measure_rows, hai_rows)
    if not records:
        logger.warning(f"No quality data found for {state} hospitals")
        return 0

    count = upsert_batch(
        table="hospital_quality",
        records=records,
        conflict_columns=["ccn"],
        update_columns=[*MEASURE_COLUMNS, "hai_sirs", "measure_period", "updated_at"],
    )

    logger.info(f"Loaded quality metrics for {count} hospitals")
    return count


if __name__ == "__main__":
    run()
