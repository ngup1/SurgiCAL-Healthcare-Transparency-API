"""
FDA MAUDE Adverse Event Extractor

Loads medical device adverse event reports from the openFDA MAUDE API.
Source: https://api.fda.gov/device/event.json

Populates the `device_adverse_events` table.
"""
from __future__ import annotations


import json

from etl.common.config import get_logger
from etl.common.db import upsert_batch, execute_query
from etl.common.http import RateLimitedClient

logger = get_logger(__name__)

OPENFDA_EVENT_URL = "https://api.fda.gov/device/event.json"


def get_known_product_codes() -> set[str]:
    """Get product codes of devices we track."""
    rows = execute_query(
        "SELECT DISTINCT fda_product_code FROM devices WHERE fda_product_code IS NOT NULL"
    )
    return {r["fda_product_code"] for r in rows}


def fetch_events(product_code: str, client: RateLimitedClient, limit: int = 100, skip: int = 0) -> list[dict]:
    """Fetch adverse events from openFDA for a product code."""
    params = {
        "search": f'device.device_report_product_code:"{product_code}"',
        "limit": limit,
        "skip": skip,
        "sort": "date_received:desc",
    }

    try:
        data = client.get(OPENFDA_EVENT_URL, params=params).json()
        return data.get("results", [])
    except Exception as e:
        logger.warning(f"Failed to fetch MAUDE events for {product_code}: {e}")
        return []


def transform_event(raw: dict) -> dict | None:
    """Transform an openFDA adverse event record into a device_adverse_events row."""
    report_key = (raw.get("mdr_report_key") or raw.get("report_number", "")).strip()
    if not report_key:
        return None

    # Extract device info (first device in the list)
    devices = raw.get("device", [])
    device_info = devices[0] if devices else {}
    openfda = device_info.get("openfda", {})

    product_codes = openfda.get("product_code", [])
    product_code = product_codes[0] if product_codes else device_info.get("product_code", "")

    # Extract event type
    event_type = _classify_event_type(raw)

    # Extract patient outcomes
    patients = raw.get("patient", [])
    outcomes = []
    for p in patients:
        for outcome in p.get("patient_outcome", []):
            outcomes.append(outcome)

    # Extract device problems
    device_problems = []
    for d in devices:
        for problem in d.get("device_report_product_code", []):
            device_problems.append(problem)

    # Build narrative from available text fields
    text_parts = raw.get("mdr_text", [])
    narrative = " ".join(
        (t.get("text", "") for t in text_parts)
    )[:2000] if text_parts else None

    return {
        "mdr_report_key": report_key,
        "product_code": product_code,
        "brand_name": (device_info.get("brand_name") or "").strip() or None,
        "manufacturer": (device_info.get("manufacturer_d_name") or "").strip() or None,
        "event_type": event_type,
        "event_date": _parse_date(raw.get("date_of_event") or raw.get("date_received")),
        "patient_outcomes": json.dumps(outcomes) if outcomes else None,
        "device_problems": json.dumps(device_problems) if device_problems else None,
        "event_narrative": narrative,
        "device_id": None,  # Linked in post-processing
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
    patients = raw.get("patient", [])
    for p in patients:
        for outcome in p.get("patient_outcome", []):
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


def run(max_events_per_code: int = 100) -> int:
    """
    Run the FDA MAUDE adverse event extractor.

    Args:
        max_events_per_code: Max events to fetch per product code (recent first).

    Returns:
        Number of adverse events loaded.
    """
    logger.info("Starting FDA MAUDE extraction")

    product_codes = get_known_product_codes()
    if not product_codes:
        logger.warning("No device product codes in database. Run fda_devices.py first.")
        return 0

    all_events: list[dict] = []
    seen_keys: set[str] = set()

    # FDA unauthenticated limit is 240 req/min = 4/sec; use 300ms delay (3.3/sec)
    with RateLimitedClient(delay_ms=300) as client:
        for pc in product_codes:
            logger.info(f"Fetching adverse events for product code: {pc}")
            skip = 0
            count_for_code = 0

            while count_for_code < max_events_per_code:
                results = fetch_events(pc, client, limit=100, skip=skip)
                if not results:
                    break

                for raw in results:
                    event = transform_event(raw)
                    if event and event["mdr_report_key"] not in seen_keys:
                        seen_keys.add(event["mdr_report_key"])
                        all_events.append(event)
                        count_for_code += 1

                if len(results) < 100:
                    break
                skip += 100

    if not all_events:
        logger.warning("No adverse events found")
        return 0

    count = upsert_batch(
        table="device_adverse_events",
        records=all_events,
        conflict_columns=["mdr_report_key"],
    )

    logger.info(f"Loaded {count} device adverse events")
    return count


if __name__ == "__main__":
    run()
