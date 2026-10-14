"""
FDA Device Recall Extractor

Loads device recall data from openFDA's bulk downloads:
- device/recall: one record per recalled product, with its 510(k)/PMA numbers
- device/enforcement: the recall's classification (Class I, II or III), which
  the recall file does not carry

Source: https://open.fda.gov/apis/device/recall/

Populates the `device_recalls` table.
"""
from __future__ import annotations

from collections.abc import Iterable

from etl.common.config import get_logger
from etl.common.db import execute_query, upsert_batch
from etl.common.http import RateLimitedClient
from etl.common.openfda import iter_bulk_records
from etl.extract.fda_devices import link_devices

logger = get_logger(__name__)

# Enforcement reports start in 2012; a recall from before then has no class on record.
CLASS_NOT_AVAILABLE = "Not available"

# device/recall statuses -> the wording enforcement reports use
STATUS_MAP = {
    "open, classified": "Ongoing",
    "open, not classified": "Ongoing",
    "terminated": "Terminated",
    "completed": "Completed",
}


def get_known_product_codes() -> set[str]:
    """Get product codes of devices we track."""
    rows = execute_query(
        "SELECT DISTINCT fda_product_code FROM devices WHERE fda_product_code IS NOT NULL"
    )
    return {r["fda_product_code"] for r in rows}


def get_premarket_numbers() -> set[str]:
    """510(k)/PMA numbers of the devices we track."""
    rows = execute_query("SELECT premarket_number FROM devices WHERE premarket_number IS NOT NULL")
    return {r["premarket_number"] for r in rows}


def build_classifications(enforcement_rows: Iterable[dict]) -> dict[str, dict]:
    """Recall number -> {classification, status} from the enforcement reports."""
    classifications: dict[str, dict] = {}
    for row in enforcement_rows:
        number = (row.get("recall_number") or "").strip()
        classification = (row.get("classification") or "").strip()
        if number and classification.startswith("Class "):
            classifications[number] = {
                "classification": classification,
                "status": (row.get("status") or "").strip() or None,
            }
    return classifications


def pick_premarket_number(raw: dict, known: set[str]) -> str | None:
    """The first 510(k)/PMA number on the recall that is a device we track, else the first one."""
    numbers = [n.strip() for n in (raw.get("k_numbers") or []) + (raw.get("pma_numbers") or []) if n and n.strip()]
    return next((n for n in numbers if n in known), numbers[0] if numbers else None)


def transform_recall(raw: dict, classifications: dict[str, dict], known_premarket: set[str]) -> dict | None:
    """Transform an openFDA recall record into a device_recalls row."""
    # product_res_number identifies one recalled product. (res_event_number is the
    # whole recall event, which can cover many products.)
    recall_number = (raw.get("product_res_number") or "").strip()
    if not recall_number:
        return None

    enforcement = classifications.get(recall_number, {})
    status = enforcement.get("status") or STATUS_MAP.get((raw.get("recall_status") or "").strip().lower())

    return {
        "recall_number": recall_number,
        "product_code": (raw.get("product_code") or "").strip() or None,
        "brand_name": (raw.get("product_description") or "").strip()[:500] or None,
        "manufacturer": (raw.get("recalling_firm") or "").strip() or None,
        "recall_class": enforcement.get("classification", CLASS_NOT_AVAILABLE),
        "reason": (raw.get("reason_for_recall") or "").strip() or None,
        "status": status,
        "recall_date": _parse_date(raw.get("event_date_initiated")),
        "termination_date": _parse_date(raw.get("event_date_terminated")),
        "quantity": (raw.get("product_quantity") or "").strip()[:500] or None,
        "distribution": (raw.get("distribution_pattern") or "").strip()[:500] or None,
        "premarket_number": pick_premarket_number(raw, known_premarket),
    }


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
    known_premarket = get_premarket_numbers()

    with RateLimitedClient(timeout=300.0) as client:
        classifications = build_classifications(iter_bulk_records(client, "enforcement"))
        logger.info(f"Read {len(classifications)} recall classifications")
        if not classifications:
            raise RuntimeError("The openFDA enforcement file had no classified recalls")

        recalls = []
        for raw in iter_bulk_records(client, "recall"):
            if (raw.get("product_code") or "").strip() not in product_codes:
                continue
            recall = transform_recall(raw, classifications, known_premarket)
            if recall:
                recalls.append(recall)

    if not recalls:
        logger.warning("No recalls found")
        return 0

    unclassified = sum(1 for r in recalls if r["recall_class"] == CLASS_NOT_AVAILABLE)
    logger.info(f"Found {len(recalls)} recalls for tracked product types ({unclassified} with no class on record)")

    count = upsert_batch(
        table="device_recalls",
        records=recalls,
        conflict_columns=["recall_number"],
        batch_size=5000,
    )
    link_devices()

    logger.info(f"Loaded {count} device recalls")
    return count


if __name__ == "__main__":
    run()
