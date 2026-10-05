"""
FDA Device Registration and 510(k)/PMA Extractor

Loads medical device data from the openFDA API:
- Device registrations and listings
- 510(k) premarket notifications
- PMA approvals

Source: https://open.fda.gov/apis/device/

Populates the `devices` table.
"""
from __future__ import annotations


from etl.common.config import get_logger
from etl.common.db import upsert_batch
from etl.common.http import RateLimitedClient

logger = get_logger(__name__)

OPENFDA_510K_URL = "https://api.fda.gov/device/510k.json"
OPENFDA_PMA_URL = "https://api.fda.gov/device/pma.json"
OPENFDA_CLASSIFICATION_URL = "https://api.fda.gov/device/classification.json"

# Surgical specialty panel codes to ingest from FDA classification database.
# These 8 panels cover all implantable and procedural surgical devices.
SURGICAL_PANELS = ["OR", "CV", "SU", "NE", "EN", "OP", "GU", "OB"]

# Human-readable panel names for logging
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

# Maps FDA advisory_committee codes to specialty strings stored in devices table
RELEVANT_SPECIALTIES = PANEL_NAMES


def fetch_product_codes_for_panels(panels: list[str], client: RateLimitedClient) -> list[str]:
    """
    Fetch all FDA product codes for the given specialty panel codes.

    Queries the openFDA device classification endpoint per panel and returns
    a sorted, deduplicated list of product codes.
    """
    product_codes: set[str] = set()

    for panel in panels:
        panel_name = PANEL_NAMES.get(panel, panel)
        logger.info(f"Fetching product codes for panel: {panel_name} ({panel})")
        skip = 0

        while True:
            params = {
                "search": f'medical_specialty:"{panel}"',
                "limit": 1000,
                "skip": skip,
            }
            try:
                data = client.get(OPENFDA_CLASSIFICATION_URL, params=params).json()
                results = data.get("results", [])
                for row in results:
                    code = (row.get("product_code") or "").strip()
                    if code:
                        product_codes.add(code)
                logger.debug(f"  {panel}: fetched {len(results)} codes at skip={skip}")
                if len(results) < 1000:
                    break
                skip += 1000
            except Exception as e:
                logger.warning(f"Failed to fetch product codes for panel {panel}: {e}")
                break

    codes = sorted(product_codes)
    logger.info(f"Discovered {len(codes)} product codes across {len(panels)} panels")
    return codes


def fetch_510k_devices(product_code: str, client: RateLimitedClient, limit: int = 100) -> list[dict]:
    """Fetch 510(k) cleared devices for a given product code."""
    params = {
        "search": f'product_code:"{product_code}"',
        "limit": limit,
        "sort": "decision_date:desc",
    }

    try:
        data = client.get(OPENFDA_510K_URL, params=params).json()
        return data.get("results", [])
    except Exception as e:
        logger.warning(f"Failed to fetch 510(k) for {product_code}: {e}")
        return []


def fetch_pma_devices(product_code: str, client: RateLimitedClient, limit: int = 100) -> list[dict]:
    """Fetch PMA approved devices for a given product code."""
    params = {
        "search": f'product_code:"{product_code}"',
        "limit": limit,
        "sort": "decision_date:desc",
    }

    try:
        data = client.get(OPENFDA_PMA_URL, params=params).json()
        return data.get("results", [])
    except Exception as e:
        logger.warning(f"Failed to fetch PMA for {product_code}: {e}")
        return []


def transform_510k(raw: dict, product_code: str) -> dict:
    """Transform a 510(k) record into a devices table row."""
    return {
        "fda_product_code": product_code,
        "brand_name": (raw.get("device_name") or raw.get("openfda", {}).get("device_name", "")).strip(),
        "generic_name": (raw.get("statement_or_summary", "") or "").strip()[:500] or None,
        "manufacturer": (raw.get("applicant") or "").strip(),
        "device_class": _get_device_class(raw),
        "medical_specialty": RELEVANT_SPECIALTIES.get(
            (raw.get("advisory_committee") or "").strip(),
            (raw.get("advisory_committee_description") or "").strip() or None,
        ),
        "premarket_number": (raw.get("k_number") or "").strip(),
        "description": (raw.get("statement_or_summary") or "").strip()[:1000] or None,
    }


def transform_pma(raw: dict, product_code: str) -> dict:
    """Transform a PMA record into a devices table row."""
    return {
        "fda_product_code": product_code,
        "brand_name": (raw.get("trade_name") or raw.get("generic_name") or "").strip(),
        "generic_name": (raw.get("generic_name") or "").strip() or None,
        "manufacturer": (raw.get("applicant") or "").strip(),
        "device_class": "III",  # PMA devices are always Class III
        "medical_specialty": RELEVANT_SPECIALTIES.get(
            (raw.get("advisory_committee") or "").strip(),
            None,
        ),
        "premarket_number": (raw.get("pma_number") or "").strip(),
        "description": None,
    }


def _get_device_class(raw: dict) -> str | None:
    """Extract device class from openFDA data."""
    openfda = raw.get("openfda", {})
    device_class = openfda.get("device_class", "")
    if device_class:
        return str(device_class).strip()
    return None


def run() -> int:
    """
    Run the FDA device extractor.

    Fetches 510(k) and PMA devices for priority surgical product codes.

    Returns:
        Number of devices loaded.
    """
    logger.info("Starting FDA device extraction")

    # FDA unauthenticated limit is 240 req/min = 4/sec; use 300ms delay (3.3/sec)
    with RateLimitedClient(delay_ms=300) as client:
        product_codes = fetch_product_codes_for_panels(SURGICAL_PANELS, client)
        if not product_codes:
            logger.error("No product codes discovered — check openFDA classification endpoint")
            return 0

        all_devices: list[dict] = []
        seen_premarket = set()

        for product_code in product_codes:
            logger.info(f"Fetching devices for product code: {product_code}")

            # Fetch 510(k) devices
            for raw in fetch_510k_devices(product_code, client):
                device = transform_510k(raw, product_code)
                if device["brand_name"] and device["manufacturer"]:
                    pn = device["premarket_number"]
                    if pn and pn not in seen_premarket:
                        seen_premarket.add(pn)
                        all_devices.append(device)

            # Fetch PMA devices
            for raw in fetch_pma_devices(product_code, client):
                device = transform_pma(raw, product_code)
                if device["brand_name"] and device["manufacturer"]:
                    pn = device["premarket_number"]
                    if pn and pn not in seen_premarket:
                        seen_premarket.add(pn)
                        all_devices.append(device)

    if not all_devices:
        logger.warning("No devices found")
        return 0

    count = upsert_batch(
        table="devices",
        records=all_devices,
        conflict_columns=["premarket_number"],
        update_columns=["fda_product_code", "brand_name", "generic_name",
                        "manufacturer", "device_class", "medical_specialty", "description", "updated_at"],
    )

    logger.info(f"Loaded {count} devices")
    return count


if __name__ == "__main__":
    run()
