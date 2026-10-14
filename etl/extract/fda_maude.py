"""
FDA MAUDE Adverse Event Extractor

Loads medical device adverse event reports from the openFDA MAUDE API.
Source: https://api.fda.gov/device/event.json

MAUDE has 26 million reports, so this keeps the most recent few per product
type instead of reading the bulk files, and uses the search API: one request
per product code. Without OPENFDA_API_KEY openFDA allows about 1,000 requests a
day, which does not cover every tracked product type; the codes in the curated
procedure map go first and the rest are left for a run with a key.

Populates the `device_adverse_events` table.
"""
from __future__ import annotations

import json

from etl.common.config import get_logger, get_settings
from etl.common.db import execute_query, upsert_batch
from etl.common.http import RateLimitedClient, openfda_params
from etl.extract.fda_devices import link_devices

logger = get_logger(__name__)

OPENFDA_EVENT_URL = "https://api.fda.gov/device/event.json"

# Most recent reports kept per product code (one request each; the API's page limit is 1000).
EVENTS_PER_CODE = 25
NARRATIVE_MAX_CHARS = 1000

# Daily request allowance left for this step: openFDA's limit with and without a key,
# minus headroom for other use of the same key or address.
REQUEST_BUDGET_WITH_KEY = 100_000
REQUEST_BUDGET_NO_KEY = 900


def get_product_codes_by_priority() -> list[str]:
    """
    Product codes of devices we track: those in the curated procedure map first,
    then the rest by number of devices.
    """
    rows = execute_query(
        """
        SELECT d.fda_product_code AS code
        FROM devices d
        WHERE d.fda_product_code IS NOT NULL
        GROUP BY d.fda_product_code
        ORDER BY EXISTS (SELECT 1 FROM procedure_product_codes p WHERE p.product_code = d.fda_product_code) DESC,
                 count(*) DESC, d.fda_product_code
        """
    )
    return [r["code"] for r in rows]


def fetch_events(product_code: str, client: RateLimitedClient, limit: int = EVENTS_PER_CODE) -> list[dict]:
    """Fetch the most recent adverse events from openFDA for a product code."""
    params = openfda_params({
        "search": f'device.device_report_product_code:"{product_code}"',
        "limit": limit,
        "sort": "date_received:desc",
    })
    # A product type with no reports answers 404, which is an empty result, not an error.
    data = client.get_json(OPENFDA_EVENT_URL, params=params, missing_ok=True)
    return data["results"] if data else []


def transform_event(raw: dict, product_code: str) -> dict | None:
    """Transform an openFDA adverse event record into a device_adverse_events row."""
    report_key = str(raw.get("mdr_report_key") or raw.get("report_number") or "").strip()
    if not report_key:
        return None

    # The device of the searched product type (a report can list several devices).
    devices = raw.get("device") or []
    device_info = next(
        (d for d in devices if d.get("device_report_product_code") == product_code),
        devices[0] if devices else {},
    )

    # Extract patient outcomes
    outcomes = []
    for p in raw.get("patient") or []:
        for outcome in p.get("sequence_number_outcome") or []:
            if outcome and outcome not in outcomes:
                outcomes.append(outcome)

    device_problems = [p for p in raw.get("product_problems") or [] if p]

    # Build narrative from available text fields
    narrative = " ".join(t.get("text", "") for t in raw.get("mdr_text") or []).strip()

    return {
        "mdr_report_key": report_key,
        "product_code": product_code,
        "brand_name": (device_info.get("brand_name") or "").strip() or None,
        "manufacturer": (device_info.get("manufacturer_d_name") or "").strip() or None,
        "event_type": _classify_event_type(raw),
        "event_date": _parse_date(raw.get("date_of_event") or raw.get("date_received")),
        "patient_outcomes": json.dumps(outcomes) if outcomes else None,
        "device_problems": json.dumps(device_problems) if device_problems else None,
        "event_narrative": narrative[:NARRATIVE_MAX_CHARS] or None,
        "premarket_number": (raw.get("pma_pmn_number") or "").strip() or None,
    }


def _classify_event_type(raw: dict) -> str:
    """Classify the event as death, injury, or malfunction."""
    event_type = (raw.get("event_type") or "").strip().lower()
    if "death" in event_type:
        return "death"
    elif "injury" in event_type:
        return "injury"
    elif "malfunction" in event_type:
        return "malfunction"

    # Fall back to checking patient outcomes
    for p in raw.get("patient") or []:
        for outcome in p.get("sequence_number_outcome") or []:
            if "death" in str(outcome).lower():
                return "death"
            if "injury" in str(outcome).lower():
                return "injury"

    return "malfunction"


def _parse_date(raw) -> str | None:
    """Parse a date string from FDA format."""
    if not raw:
        return None
    raw = str(raw).strip()
    if len(raw) >= 8 and raw[:8].isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return raw[:10] if len(raw) >= 10 else None


def run(max_events_per_code: int = EVENTS_PER_CODE, max_requests: int | None = None) -> int:
    """
    Run the FDA MAUDE adverse event extractor.

    Args:
        max_events_per_code: Max events to fetch per product code (recent first).
        max_requests: Request allowance; defaults to openFDA's daily limit for
            the configured key (or for no key).

    Returns:
        Number of adverse events loaded.
    """
    logger.info("Starting FDA MAUDE extraction")

    product_codes = get_product_codes_by_priority()
    if not product_codes:
        logger.warning("No device product codes in database. Run fda_devices.py first.")
        return 0

    has_key = bool(get_settings().openfda_api_key)
    if max_requests is None:
        max_requests = REQUEST_BUDGET_WITH_KEY if has_key else REQUEST_BUDGET_NO_KEY
    selected = product_codes[:max_requests]
    if len(selected) < len(product_codes):
        logger.warning(
            f"Loading adverse events for {len(selected)} of {len(product_codes)} product codes: "
            f"the request allowance is {max_requests}"
            + ("" if has_key else " without OPENFDA_API_KEY")
        )

    all_events: dict[str, dict] = {}

    # openFDA allows 240 requests a minute (4 a second); 300 ms between requests stays under it.
    # A request that fails after retries raises: a partial load would look complete.
    with RateLimitedClient(delay_ms=300) as client:
        for i, pc in enumerate(selected, 1):
            for raw in fetch_events(pc, client, limit=max_events_per_code):
                event = transform_event(raw, pc)
                if event:
                    all_events.setdefault(event["mdr_report_key"], event)
            if i % 200 == 0:
                logger.info(f"  {i} of {len(selected)} product codes read, {len(all_events)} events")

    if not all_events:
        logger.warning("No adverse events found")
        return 0

    count = upsert_batch(
        table="device_adverse_events",
        records=list(all_events.values()),
        conflict_columns=["mdr_report_key"],
        batch_size=2000,
    )
    link_devices()

    logger.info(f"Loaded {count} device adverse events")
    return count


if __name__ == "__main__":
    run()
