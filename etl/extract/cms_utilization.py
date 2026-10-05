"""
CMS Medicare Physician Utilization Extractor

Loads physician volume and payment data from the Medicare Physician & Other
Practitioners dataset. Used to compute wRVU estimates and volume buckets.

Source: https://data.cms.gov/provider-summary-by-type-of-service/
       medicare-physician-other-practitioners/
       medicare-physician-other-practitioners-by-provider-and-service

Populates volume-related fields in the `provider_metrics` table.
"""
from __future__ import annotations


import pandas as pd

from etl.common.config import get_logger
from etl.common.db import upsert_batch, execute_query
from etl.common.http import fetch_csv, RateLimitedClient

logger = get_logger(__name__)

# The Medicare Physician & Other Practitioners - By Provider and Service dataset.
# Dataset UUID: 92396110-2aed-4d63-a6a2-5d6207d46a29 (2023 data, published 2025)
# The UUID changes when CMS publishes new annual data — check catalog.data.gov.
CMS_UTILIZATION_URL = (
    "https://data.cms.gov/data-api/v1/dataset/92396110-2aed-4d63-a6a2-5d6207d46a29/data"
)

# Volume bucket thresholds (total annual Medicare services)
VOLUME_THRESHOLDS = {
    "LOW": 50,
    "MEDIUM": 200,
    # HIGH > 200
}

# CMS dataset year — update when new annual data is published
MEASURE_YEAR = 2023


def get_ca_npis() -> set[str]:
    """Get the set of CA provider NPIs already in the database."""
    rows = execute_query("SELECT npi FROM providers WHERE state = 'CA'")
    return {r["npi"] for r in rows}


def get_wrvu_lookup() -> dict[str, float]:
    """Get CPT -> avg_work_rvu mapping from cpt_codes table."""
    rows = execute_query(
        "SELECT code, avg_work_rvu FROM cpt_codes WHERE avg_work_rvu IS NOT NULL"
    )
    return {r["code"]: float(r["avg_work_rvu"]) for r in rows}


def classify_volume(total_services: int) -> str:
    """Classify total services into a volume bucket."""
    if total_services < VOLUME_THRESHOLDS["LOW"]:
        return "LOW"
    elif total_services < VOLUME_THRESHOLDS["MEDIUM"]:
        return "MEDIUM"
    return "HIGH"


def fetch_utilization_by_provider(
    ca_npis: set[str],
) -> tuple[dict[str, dict], list[dict]]:
    """
    Fetch utilization data from CMS.

    Returns:
        provider_data: aggregated totals per NPI (for provider_metrics)
        procedure_rows: per-NPI × per-CPT rows (for provider_procedure_volumes)
    """
    logger.info("Fetching Medicare utilization data...")

    wrvu_lookup = get_wrvu_lookup()

    provider_data: dict[str, dict] = {}
    procedure_rows: list[dict] = []
    offset = 0
    page_size = 1000

    while True:
        try:
            with RateLimitedClient(delay_ms=200) as client:
                response = client.get(
                    CMS_UTILIZATION_URL,
                    params={"offset": offset, "size": page_size},
                )
                result = response.json()

            # data-api/v1 returns a plain JSON array; older endpoints wrap in {"results": []}
            rows = result if isinstance(result, list) else result.get("results", [])
            if not rows:
                break

            for row in rows:
                npi = str(row.get("rndrng_npi") or row.get("npi", "")).strip()
                if npi not in ca_npis:
                    continue

                hcpcs = str(row.get("hcpcs_cd") or row.get("hcpcs_code", "")).strip()
                services = _safe_int(row.get("tot_srvcs") or row.get("total_services"))
                beneficiaries = _safe_int(row.get("tot_benes") or row.get("total_beneficiaries"))
                payment = _safe_float(row.get("avg_mdcr_pymt_amt") or row.get("average_medicare_payment"))

                # Aggregate into provider-level totals
                if npi not in provider_data:
                    provider_data[npi] = {
                        "npi": npi,
                        "total_medicare_services": 0,
                        "total_medicare_beneficiaries": 0,
                        "total_medicare_payment": 0.0,
                        "wrvu_estimate": 0.0,
                    }

                pdata = provider_data[npi]
                pdata["total_medicare_services"] += services or 0
                pdata["total_medicare_beneficiaries"] += beneficiaries or 0
                pdata["total_medicare_payment"] += (payment or 0.0) * (services or 0)

                cpt_wrvu = wrvu_lookup.get(hcpcs, 0.0)
                if hcpcs in wrvu_lookup and services:
                    pdata["wrvu_estimate"] += cpt_wrvu * services

                # Store per-CPT row for provider_procedure_volumes
                if hcpcs:
                    procedure_rows.append({
                        "npi": npi,
                        "cpt": hcpcs,
                        "total_services": services,
                        "total_beneficiaries": beneficiaries,
                        "total_payment": round((payment or 0.0) * (services or 0), 2),
                        "wrvu_estimate": round(cpt_wrvu * (services or 0), 2),
                        "measure_year": MEASURE_YEAR,
                    })

            logger.debug(f"Processed utilization page (offset={offset}, {len(rows)} rows)")
            if len(rows) < page_size:
                break
            offset += page_size

        except Exception as e:
            logger.error(f"Error fetching utilization at offset {offset}: {e}")
            break

    return provider_data, procedure_rows


def _safe_int(val) -> int | None:
    if val is None:
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def run() -> int:
    """
    Run the Medicare utilization extractor.

    Fetches utilization data for CA providers and upserts volume metrics
    into provider_metrics.

    Returns:
        Number of provider metrics updated.
    """
    logger.info("Starting CMS utilization extraction")

    ca_npis = get_ca_npis()
    if not ca_npis:
        logger.warning("No CA providers in database. Run cms_physicians.py first.")
        return 0

    logger.info(f"Looking up utilization for {len(ca_npis)} CA providers")
    provider_data, procedure_rows = fetch_utilization_by_provider(ca_npis)

    if not provider_data:
        logger.warning("No utilization data found for CA providers")
        return 0

    # Add volume bucket classification
    records = []
    for npi, data in provider_data.items():
        data["volume_bucket"] = classify_volume(data["total_medicare_services"])
        data["wrvu_estimate"] = round(data["wrvu_estimate"], 2)
        data["total_medicare_payment"] = round(data["total_medicare_payment"], 2)
        records.append(data)

    count = upsert_batch(
        table="provider_metrics",
        records=records,
        conflict_columns=["npi"],
        update_columns=[
            "total_medicare_services",
            "total_medicare_beneficiaries",
            "total_medicare_payment",
            "wrvu_estimate",
            "volume_bucket",
            "last_refreshed",
        ],
    )
    logger.info(f"Updated utilization metrics for {count} providers")

    # Upsert per-procedure volumes
    proc_count = upsert_batch(
        table="provider_procedure_volumes",
        records=procedure_rows,
        conflict_columns=["npi", "cpt", "measure_year"],
        update_columns=["total_services", "total_beneficiaries", "total_payment", "wrvu_estimate", "updated_at"],
    )
    logger.info(f"Upserted {proc_count} provider procedure volume rows")

    return count


if __name__ == "__main__":
    run()
