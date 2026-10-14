"""
FDA 510(k)/PMA Device Extractor

Loads medical device data from openFDA's bulk downloads:
- Device classification (product codes, device class, specialty panel)
- 510(k) premarket notifications
- PMA approvals

Source: https://open.fda.gov/apis/device/

Populates the `devices` table.
"""
from __future__ import annotations

from collections.abc import Iterable

from etl.common.config import get_logger
from etl.common.db import execute, upsert_batch
from etl.common.http import RateLimitedClient
from etl.common.openfda import iter_bulk_records
from etl.extract.device_procedures import load_curated_map

logger = get_logger(__name__)

# Surgical specialty panel codes to ingest from FDA classification database.
# These 8 panels cover all implantable and procedural surgical devices.
SURGICAL_PANELS = ["OR", "CV", "SU", "NE", "EN", "OP", "GU", "OB"]

# Panel names stored as devices.medical_specialty
PANEL_NAMES = {
    "OR": "Orthopedic",
    "CV": "Cardiovascular",
    "SU": "General/Plastic Surgery",
    "NE": "Neurology",
    "EN": "Ear, Nose, Throat",
    "OP": "Ophthalmic",
    "GU": "Gastroenterology/Urology",
    "OB": "Obstetrics/Gynecology",
}

# openFDA writes the device class as a digit; the API and PMA records use roman numerals.
DEVICE_CLASSES = {"1": "I", "2": "II", "3": "III"}


def build_product_types(classification_rows: Iterable[dict], extra_codes: set[str]) -> dict[str, dict]:
    """
    Product code -> {name, device_class, specialty} for the surgical panels,
    plus any `extra_codes` (the curated procedure map's) whatever their panel.

    The panel is the review panel when the product type has no regulation of its
    own: most class III devices, such as coronary stents, are filed that way and
    have an empty medical_specialty.
    """
    product_types: dict[str, dict] = {}
    for row in classification_rows:
        code = (row.get("product_code") or "").strip()
        panel = (row.get("medical_specialty") or row.get("review_panel") or "").strip()
        if not code or (panel not in SURGICAL_PANELS and code not in extra_codes):
            continue
        product_types[code] = {
            "name": (row.get("device_name") or "").strip() or None,
            "device_class": DEVICE_CLASSES.get(str(row.get("device_class") or "").strip()),
            "specialty": PANEL_NAMES.get(panel) or (row.get("medical_specialty_description") or "").strip() or None,
            "definition": (row.get("definition") or "").strip()[:1000] or None,
        }
    return product_types


def transform_510k(raw: dict, product_types: dict[str, dict]) -> dict | None:
    """Transform a 510(k) record into a devices table row (None if it isn't a tracked product type)."""
    product_code = (raw.get("product_code") or "").strip()
    product_type = product_types.get(product_code)
    brand_name = (raw.get("device_name") or "").strip()
    manufacturer = (raw.get("applicant") or "").strip()
    premarket_number = (raw.get("k_number") or "").strip()
    if product_type is None or not brand_name or not manufacturer or not premarket_number:
        return None
    return {
        "fda_product_code": product_code,
        "brand_name": brand_name[:500],
        # The product type's name. (statement_or_summary only says whether a
        # summary document exists; it is the literal word "Summary".)
        "generic_name": product_type["name"],
        "manufacturer": manufacturer,
        "device_class": product_type["device_class"],
        "medical_specialty": product_type["specialty"],
        "premarket_number": premarket_number,
        "description": product_type["definition"],
    }


def transform_pma(raw: dict, product_types: dict[str, dict]) -> dict | None:
    """Transform a PMA record into a devices table row (None if it isn't a tracked product type)."""
    product_code = (raw.get("product_code") or "").strip()
    product_type = product_types.get(product_code)
    brand_name = (raw.get("trade_name") or raw.get("generic_name") or "").strip()
    manufacturer = (raw.get("applicant") or "").strip()
    premarket_number = (raw.get("pma_number") or "").strip()
    if product_type is None or not brand_name or not manufacturer or not premarket_number:
        return None
    return {
        "fda_product_code": product_code,
        "brand_name": brand_name[:500],
        "generic_name": (raw.get("generic_name") or "").strip() or product_type["name"],
        "manufacturer": manufacturer,
        "device_class": "III",  # PMA devices are always Class III
        "medical_specialty": product_type["specialty"],
        "premarket_number": premarket_number,
        "description": product_type["definition"],
    }


def collect_pma_devices(pma_rows: Iterable[dict], product_types: dict[str, dict]) -> list[dict]:
    """
    One device per PMA number.

    The PMA file has a record for the original approval and one for every later
    supplement (57,000 records for about 1,500 devices). The original approval
    names the device; a supplement is used only when the original is missing.
    """
    devices: dict[str, tuple[bool, dict]] = {}
    for raw in pma_rows:
        device = transform_pma(raw, product_types)
        if device is None:
            continue
        is_original = not (raw.get("supplement_number") or "").strip()
        existing = devices.get(device["premarket_number"])
        if existing is None or (is_original and not existing[0]):
            devices[device["premarket_number"]] = (is_original, device)
    return [device for _, device in devices.values()]


def link_devices() -> dict[str, int]:
    """
    Point recalls and adverse events at their device.

    A report is linked by the 510(k)/PMA number it names. An adverse event without
    a usable number falls back to a device of the same product type from the same
    manufacturer (compared on the first word of the name); with no manufacturer
    match it stays unlinked instead of being pinned on another company's device.

    Runs after any of the device, recall or adverse-event loads.
    """
    linked = {}
    for table in ("device_recalls", "device_adverse_events"):
        linked[table] = execute(
            f"""
            UPDATE {table} t SET device_id = d.id
            FROM devices d
            WHERE d.premarket_number = t.premarket_number AND t.device_id IS DISTINCT FROM d.id
            """
        )
    linked["device_adverse_events_by_manufacturer"] = execute(
        """
        UPDATE device_adverse_events e SET device_id = m.id
        FROM (
          SELECT DISTINCT ON (fda_product_code, maker) id, fda_product_code, maker
          FROM (SELECT id, fda_product_code, premarket_number,
                       lower(split_part(regexp_replace(manufacturer, '[^A-Za-z0-9 ]', '', 'g'), ' ', 1)) AS maker
                FROM devices) d
          ORDER BY fda_product_code, maker, premarket_number DESC
        ) m
        WHERE e.device_id IS NULL
          AND e.product_code = m.fda_product_code
          AND lower(split_part(regexp_replace(e.manufacturer, '[^A-Za-z0-9 ]', '', 'g'), ' ', 1)) = m.maker
          AND m.maker <> ''
        """
    )
    logger.info(f"Linked reports to devices: {linked}")
    return linked


def run() -> int:
    """
    Run the FDA device extractor.

    Loads every 510(k) clearance and PMA approval whose product type is in a
    surgical panel or in the curated procedure map.

    Returns:
        Number of devices loaded.
    """
    logger.info("Starting FDA device extraction")
    curated_codes = {row["product_code"] for row in load_curated_map()}

    with RateLimitedClient(timeout=300.0) as client:
        product_types = build_product_types(iter_bulk_records(client, "classification"), curated_codes)
        if not product_types:
            raise RuntimeError("No product codes found in the openFDA classification file")
        logger.info(f"Tracking {len(product_types)} product types across {len(SURGICAL_PANELS)} panels")

        devices = collect_pma_devices(iter_bulk_records(client, "pma"), product_types)
        logger.info(f"Found {len(devices)} PMA devices")

        cleared = 0
        for raw in iter_bulk_records(client, "510k"):
            device = transform_510k(raw, product_types)
            if device:
                devices.append(device)
                cleared += 1
        logger.info(f"Found {cleared} 510(k) devices")

    if not devices:
        logger.warning("No devices found")
        return 0

    count = upsert_batch(
        table="devices",
        records=devices,
        conflict_columns=["premarket_number"],
        update_columns=["fda_product_code", "brand_name", "generic_name",
                        "manufacturer", "device_class", "medical_specialty", "description", "updated_at"],
        batch_size=5000,
    )
    link_devices()

    logger.info(f"Loaded {count} devices")
    return count


if __name__ == "__main__":
    run()
