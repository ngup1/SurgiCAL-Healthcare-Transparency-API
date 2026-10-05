"""
FDA Device Recall Extractor

Loads device recall data from the openFDA API.
Source: https://api.fda.gov/device/recall.json

Populates the `device_recalls` table.
"""
from __future__ import annotations


from etl.common.config import get_logger
from etl.common.db import upsert_batch, execute_query
from etl.common.http import fetch_json

logger = get_logger(__name__)

OPENFDA_RECALL_URL = "https://api.fda.gov/device/recall.json"


def get_known_product_codes() -> set[str]:
    """Get product codes of devices we track."""
    rows = execute_query(
        "SELECT DISTINCT fda_product_code FROM devices WHERE fda_product_code IS NOT NULL"
    )
    return {r["fda_product_code"] for r in rows}


def fetch_recalls(product_code: str, limit: int = 100, skip: int = 0) -> list[dict]:
    """Fetch recalls from openFDA for a product code."""
    params = {
        "search": f'product_code:"{product_code}"',
        "limit": limit,
        "skip": skip,
        "sort": "event_date_terminated:desc",
    }

    try:
        data = fetch_json(OPENFDA_RECALL_URL, params=params)
        return data.get("results", [])
    except Exception as e:
        logger.warning(f"Failed to fetch recalls for {product_code}: {e}")
        return []


def transform_recall(raw: dict) -> dict | None:
    """Transform an openFDA recall record into a device_recalls row."""
    recall_number = (
        raw.get("res_event_number") or
        raw.get("recall_number", "")
    ).strip()
    if not recall_number:
        return None

    openfda = raw.get("openfda", {})
    product_codes = openfda.get("product_code", [])
    product_code = product_codes[0] if product_codes else raw.get("product_code", "")

    return {
        "recall_number": recall_number,
        "product_code": product_code,
        "brand_name": (raw.get("product_description") or "").strip()[:500] or None,
        "manufacturer": (raw.get("firm_fei_number") or raw.get("recalling_firm", "")).strip() or None,
        "recall_class": _parse_class(raw.get("event_date_terminated") or raw.get("classification", "")),
        "reason": (raw.get("reason_for_recall") or "").strip() or None,
        "status": _parse_status(raw.get("status", "")),
        "recall_date": _parse_date(raw.get("event_date_initiated")),
        "termination_date": _parse_date(raw.get("event_date_terminated")),
        "quantity": (raw.get("product_quantity") or "").strip() or None,
        "distribution": (raw.get("distribution_pattern") or "").strip()[:500] or None,
        "device_id": None,  # Linked in a post-processing step
    }


def _parse_class(raw: str) -> str:
    """Parse recall classification to I, II, or III."""
    raw = str(raw).strip().upper()
    if "I" in raw and "II" not in raw:
        return "I"
    elif "III" in raw:
        return "III"
    elif "II" in raw:
        return "II"
    return "II"  # Default to Class II


def _parse_status(raw: str) -> str:
    """Parse recall status."""
    raw = raw.strip().lower()
    if "terminated" in raw or "completed" in raw:
        return "terminated"
    elif "ongoing" in raw or "open" in raw:
        return "ongoing"
    return "ongoing"


def _parse_date(raw) -> str | None:
    """Parse a date string from FDA format (YYYYMMDD or ISO)."""
    if not raw:
        return None
    raw = str(raw).strip()
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return raw[:10] if len(raw) >= 10 else None


def run() -> int:
    """
    Run the FDA recall extractor for tracked device product codes.

    Returns:
        Number of recalls loaded.
    """
    logger.info("Starting FDA recall extraction")

    product_codes = get_known_product_codes()
    if not product_codes:
        logger.warning("No device product codes in database. Run fda_devices.py first.")
        return 0

    all_recalls: list[dict] = []
    seen_numbers: set[str] = set()

    for pc in product_codes:
        logger.info(f"Fetching recalls for product code: {pc}")
        skip = 0
        while True:
            results = fetch_recalls(pc, limit=100, skip=skip)
            if not results:
                break

            for raw in results:
                recall = transform_recall(raw)
                if recall and recall["recall_number"] not in seen_numbers:
                    seen_numbers.add(recall["recall_number"])
                    all_recalls.append(recall)

            if len(results) < 100:
                break
            skip += 100

    if not all_recalls:
        logger.warning("No recalls found")
        return 0

    count = upsert_batch(
        table="device_recalls",
        records=all_recalls,
        conflict_columns=["recall_number"],
    )

    logger.info(f"Loaded {count} device recalls")
    return count


if __name__ == "__main__":
    run()
