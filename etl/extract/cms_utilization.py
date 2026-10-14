"""
CMS Medicare Physician Utilization Extractor

Loads physician volume and payment data from the Medicare Physician & Other
Practitioners dataset. Used to compute wRVU estimates and volume buckets.

Source: https://data.cms.gov/provider-summary-by-type-of-service/
       medicare-physician-other-practitioners/
       medicare-physician-other-practitioners-by-provider-and-service

Populates volume-related fields in the `provider_metrics` table and the
`provider_procedure_volumes` table.
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator

from etl.common.config import get_logger, get_settings
from etl.common.db import execute_query, upsert_batch
from etl.common.http import RateLimitedClient

logger = get_logger(__name__)

# The Medicare Physician & Other Practitioners - By Provider and Service dataset.
# The dataset ID changes when CMS publishes new annual data; it and the year are
# the CMS_UTILIZATION_DATASET_ID and CMS_UTILIZATION_YEAR settings.
CMS_UTILIZATION_URL = "https://data.cms.gov/data-api/v1/dataset/{dataset_id}/data"

# The only columns read; asking for just these makes each page about a tenth the size.
COLUMNS = ["Rndrng_NPI", "HCPCS_Cd", "Place_Of_Srvc", "Tot_Benes", "Tot_Srvcs", "Avg_Mdcr_Pymt_Amt"]
PAGE_SIZE = 5000

# Volume bucket thresholds (total annual Medicare services)
VOLUME_THRESHOLDS = {
    "LOW": 50,
    "MEDIUM": 200,
    # HIGH > 200
}


def get_state_npis(state: str) -> set[str]:
    """Get the set of provider NPIs for the state already in the database."""
    rows = execute_query("SELECT npi FROM providers WHERE state = %s", (state,))
    return {r["npi"] for r in rows}


def get_wrvu_lookup() -> dict[str, float | None]:
    """Get CPT -> avg_work_rvu for every code in cpt_codes (None where CMS assigns no work RVU)."""
    rows = execute_query("SELECT code, avg_work_rvu FROM cpt_codes")
    return {r["code"]: float(r["avg_work_rvu"]) if r["avg_work_rvu"] is not None else None for r in rows}


def classify_volume(total_services: int) -> str:
    """Classify total services into a volume bucket."""
    if total_services < VOLUME_THRESHOLDS["LOW"]:
        return "LOW"
    elif total_services < VOLUME_THRESHOLDS["MEDIUM"]:
        return "MEDIUM"
    return "HIGH"


def fetch_utilization_rows(client: RateLimitedClient, state: str) -> Iterator[dict]:
    """
    Page through the utilization dataset for one state.

    The state filter runs on the server, so this reads the state's rows
    (about 830,000 for CA) instead of the national file's 9.8 million.
    Errors are raised, not skipped: half a year of volumes is worse than none.
    """
    url = CMS_UTILIZATION_URL.format(dataset_id=get_settings().cms_utilization_dataset_id)
    offset = 0
    while True:
        rows = client.get_json(
            url,
            params={
                "filter[Rndrng_Prvdr_State_Abrvtn]": state,
                "column": ",".join(COLUMNS),
                "size": PAGE_SIZE,
                "offset": offset,
            },
        )
        if not isinstance(rows, list):
            raise RuntimeError(f"Unexpected utilization response at offset {offset}: {str(rows)[:200]}")
        yield from rows
        logger.debug(f"Processed utilization page (offset={offset}, {len(rows)} rows)")
        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        if offset % 100_000 == 0:
            logger.info(f"  {offset} utilization rows read")


def aggregate_utilization(
    rows: Iterable[dict],
    npis: set[str],
    wrvu_lookup: dict[str, float | None],
    measure_year: int,
) -> tuple[list[dict], list[dict]]:
    """
    Aggregate raw utilization rows.

    CMS reports a provider's procedure once per place of service (facility and
    office), so rows are summed into one per provider and procedure. Only
    providers in `npis` and codes in `wrvu_lookup` (the cpt_codes table) are
    kept; that drops drug and supply codes, which are not procedures.

    Returns:
        provider_records: totals per NPI (for provider_metrics)
        procedure_records: per-NPI × per-CPT rows (for provider_procedure_volumes)
    """
    by_procedure: dict[tuple[str, str], dict] = {}

    for row in rows:
        npi = str(row.get("Rndrng_NPI") or "").strip()
        hcpcs = str(row.get("HCPCS_Cd") or "").strip()
        if npi not in npis or hcpcs not in wrvu_lookup:
            continue

        services = _safe_float(row.get("Tot_Srvcs")) or 0.0
        beneficiaries = _safe_int(row.get("Tot_Benes")) or 0
        payment = _safe_float(row.get("Avg_Mdcr_Pymt_Amt")) or 0.0

        agg = by_procedure.setdefault((npi, hcpcs), {"services": 0.0, "beneficiaries": 0, "payment": 0.0})
        agg["services"] += services
        agg["beneficiaries"] += beneficiaries
        agg["payment"] += payment * services

    providers: dict[str, dict] = {}
    procedure_records: list[dict] = []

    for (npi, hcpcs), agg in by_procedure.items():
        services = round(agg["services"])
        wrvu = (wrvu_lookup[hcpcs] or 0.0) * agg["services"]
        procedure_records.append({
            "npi": npi,
            "cpt": hcpcs,
            "total_services": services,
            "total_beneficiaries": agg["beneficiaries"],
            "total_payment": round(agg["payment"], 2),
            "wrvu_estimate": round(wrvu, 2),
            "measure_year": measure_year,
        })

        pdata = providers.setdefault(npi, {
            "npi": npi,
            "total_medicare_services": 0,
            "total_medicare_beneficiaries": 0,
            "total_medicare_payment": 0.0,
            "wrvu_estimate": 0.0,
        })
        pdata["total_medicare_services"] += services
        pdata["total_medicare_beneficiaries"] += agg["beneficiaries"]
        pdata["total_medicare_payment"] += agg["payment"]
        pdata["wrvu_estimate"] += wrvu

    provider_records = []
    for pdata in providers.values():
        pdata["volume_bucket"] = classify_volume(pdata["total_medicare_services"])
        pdata["wrvu_estimate"] = round(pdata["wrvu_estimate"], 2)
        pdata["total_medicare_payment"] = round(pdata["total_medicare_payment"], 2)
        provider_records.append(pdata)

    return provider_records, procedure_records


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


def run(state: str = "CA") -> int:
    """
    Run the Medicare utilization extractor.

    Fetches utilization data for the state's providers and upserts volume
    metrics into provider_metrics and provider_procedure_volumes.

    Returns:
        Number of provider metrics updated.
    """
    settings = get_settings()
    measure_year = settings.cms_utilization_year
    logger.info(f"Starting CMS utilization extraction ({measure_year} data, state={state})")

    npis = get_state_npis(state)
    if not npis:
        logger.warning(f"No {state} providers in database. Run cms_physicians.py first.")
        return 0

    logger.info(f"Looking up utilization for {len(npis)} {state} providers")
    with RateLimitedClient(delay_ms=200, timeout=120.0) as client:
        provider_records, procedure_records = aggregate_utilization(
            fetch_utilization_rows(client, state), npis, get_wrvu_lookup(), measure_year
        )

    if not provider_records:
        logger.warning(f"No utilization data found for {state} providers")
        return 0

    count = upsert_batch(
        table="provider_metrics",
        records=provider_records,
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
        records=procedure_records,
        conflict_columns=["npi", "cpt", "measure_year"],
        update_columns=["total_services", "total_beneficiaries", "total_payment", "wrvu_estimate", "updated_at"],
        batch_size=5000,
    )
    logger.info(f"Upserted {proc_count} provider procedure volume rows")

    return count


if __name__ == "__main__":
    run()
