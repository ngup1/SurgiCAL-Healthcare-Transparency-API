"""
CMS Hospital General Information Extractor

Loads core hospital data from the CMS Hospital General Information dataset.
Source: https://data.cms.gov/provider-data/dataset/xubh-q36u

Populates the `hospitals` table with CCN, name, location, type, ownership.
"""

from etl.common.config import get_logger
from etl.common.db import upsert_batch
from etl.common.http import post_json

logger = get_logger(__name__)

# CMS Hospital General Information API (Socrata-style)
CMS_HOSPITAL_INFO_URL = (
    "https://data.cms.gov/provider-data/api/1/datastore/query/xubh-q36u/0"
)

# Map CMS ownership codes to our simplified categories
OWNERSHIP_MAP = {
    "Government - Federal": "government",
    "Government - Hospital District or Authority": "government",
    "Government - Local": "government",
    "Government - State": "government",
    "Proprietary": "proprietary",
    "Voluntary non-profit - Church": "voluntary_nonprofit",
    "Voluntary non-profit - Other": "voluntary_nonprofit",
    "Voluntary non-profit - Private": "voluntary_nonprofit",
    "Physician": "proprietary",
    "Tribal": "government",
}


def fetch_hospitals(state: str = "CA", offset: int = 0, limit: int = 500) -> list[dict]:
    """
    Fetch hospitals from CMS Hospital General Information API.

    The API supports pagination via POST body with conditions, limit, and offset.
    """
    body = {
        "conditions": [
            {"property": "state", "value": state, "operator": "="}
        ],
        "limit": limit,
        "offset": offset,
        "sort": {"property": "facility_id", "order": "asc"},
        "results": True,
        "schema": False,
        "keys": True,
        "rowIds": False,
    }

    response = post_json(CMS_HOSPITAL_INFO_URL, body)
    return response.get("results", [])


def transform_hospital(raw: dict) -> dict:
    """Transform a CMS hospital record into our schema format."""
    # CMS field names vary by dataset version; handle common variants
    ccn = raw.get("facility_id") or raw.get("provider_id") or raw.get("ccn", "")
    name = raw.get("facility_name") or raw.get("hospital_name", "")
    address = raw.get("address") or raw.get("address_1", "")
    city = raw.get("city") or raw.get("city_town", "")
    state = raw.get("state", "")
    zip_code = raw.get("zip_code") or raw.get("zip", "")
    phone = raw.get("phone_number") or raw.get("telephone", "")
    hospital_type = raw.get("hospital_type", "")
    ownership_raw = raw.get("hospital_ownership", "")
    emergency = raw.get("emergency_services", "No")

    # Parse lat/lng if available
    lat = raw.get("location", {}).get("latitude") if isinstance(raw.get("location"), dict) else None
    lng = raw.get("location", {}).get("longitude") if isinstance(raw.get("location"), dict) else None

    record = {
        "ccn": ccn.strip(),
        "name": name.strip(),
        "address": address.strip(),
        "city": city.strip(),
        "state": state.strip(),
        "zip": zip_code.strip(),
        "phone": phone.strip() if phone else None,
        "hospital_type": hospital_type.strip().lower().replace(" - ", "_").replace(" ", "_") if hospital_type else None,
        "ownership": OWNERSHIP_MAP.get(ownership_raw.strip(), ownership_raw.strip().lower()) if ownership_raw else None,
        "emergency_services": emergency.strip().lower() == "yes" if emergency else False,
    }

    # Build PostGIS point if we have coordinates
    if lat and lng:
        record["location"] = f"SRID=4326;POINT({lng} {lat})"
    else:
        record["location"] = None

    return record


def run(state: str = "CA") -> int:
    """
    Run the CMS hospital extractor.

    Fetches all hospitals for the given state and upserts into the hospitals table.

    Args:
        state: Two-letter state code (default: CA for MVP).

    Returns:
        Number of hospitals loaded.
    """
    logger.info(f"Starting CMS hospital extraction for state={state}")

    all_records = []
    offset = 0
    page_size = 500

    while True:
        raw_results = fetch_hospitals(state=state, offset=offset, limit=page_size)
        if not raw_results:
            break

        for raw in raw_results:
            record = transform_hospital(raw)
            if record["ccn"] and record["name"]:
                all_records.append(record)

        logger.info(f"Fetched {len(raw_results)} hospitals (offset={offset})")

        if len(raw_results) < page_size:
            break
        offset += page_size

    if not all_records:
        logger.warning("No hospitals found")
        return 0

    count = upsert_batch(
        table="hospitals",
        records=all_records,
        conflict_columns=["ccn"],
    )

    logger.info(f"Loaded {count} hospitals for state={state}")
    return count


if __name__ == "__main__":
    run()
