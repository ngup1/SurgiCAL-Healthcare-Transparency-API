"""
CMS Hospital General Information Extractor

Loads core hospital data from the CMS Hospital General Information dataset.
Source: https://data.cms.gov/provider-data/dataset/xubh-q36u

Populates the `hospitals` table with CCN, name, location, type, ownership, and
the star rating and comparison groups of `hospital_quality`, which come from
the same dataset.
"""
from __future__ import annotations

from etl.common.config import get_logger
from etl.common.db import upsert_batch
from etl.common.http import RateLimitedClient
from etl.common.places import PlaceIndex, add_postal_cities

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
    "Veterans Health Administration": "government",
    "Department of Defense": "government",
    "Proprietary": "proprietary",
    "Voluntary non-profit - Church": "voluntary_nonprofit",
    "Voluntary non-profit - Other": "voluntary_nonprofit",
    "Voluntary non-profit - Private": "voluntary_nonprofit",
    "Physician": "proprietary",
    "Tribal": "government",
}

# Map CMS hospital types to the values the API documents
HOSPITAL_TYPE_MAP = {
    "Acute Care Hospitals": "acute_care",
    "Critical Access Hospitals": "critical_access",
    "Childrens": "childrens",
    "Psychiatric": "psychiatric",
    "Acute Care - Veterans Administration": "veterans",
    "Acute Care - Department of Defense": "military",
    "Long-term": "long_term",
    "Rural Emergency Hospital": "rural_emergency",
}

# hospital_quality group column -> the measure-group prefix CMS uses in its count fields
GROUP_PREFIXES = {
    "mortality_group": "mort",
    "safety_group": "safety",
    "readmission_group": "readm",
}


def fetch_hospitals(client: RateLimitedClient, state: str = "CA", offset: int = 0, limit: int = 500) -> list[dict]:
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

    response = client.post_json(CMS_HOSPITAL_INFO_URL, body)
    return response["results"]


def _slug(value: str) -> str:
    return value.strip().lower().replace(" - ", "_").replace("-", "_").replace(" ", "_")


def transform_hospital(raw: dict, places: PlaceIndex) -> dict:
    """Transform a CMS hospital record into our schema format."""
    city = places.city_name(raw.get("citytown")) or ""
    zip_code = (raw.get("zip_code") or "").strip()
    phone = (raw.get("telephone_number") or "").strip()
    hospital_type = (raw.get("hospital_type") or "").strip()
    ownership_raw = (raw.get("hospital_ownership") or "").strip()

    return {
        "ccn": (raw.get("facility_id") or "").strip(),
        "name": (raw.get("facility_name") or "").strip(),
        "address": (raw.get("address") or "").strip(),
        "city": city,
        "state": (raw.get("state") or "").strip(),
        "zip": zip_code,
        "phone": phone or None,
        "hospital_type": HOSPITAL_TYPE_MAP.get(hospital_type, _slug(hospital_type)) if hospital_type else None,
        "ownership": OWNERSHIP_MAP.get(ownership_raw, _slug(ownership_raw)) if ownership_raw else None,
        "emergency_services": (raw.get("emergency_services") or "").strip().lower() == "yes",
        # CMS publishes no coordinates; use the centre of the hospital's ZIP code.
        "location": places.locate(zip_code, city),
    }


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def comparison_group(raw: dict, prefix: str) -> str | None:
    """
    'above', 'same' or 'below' the national average for one measure group.

    CMS no longer publishes a single comparison per group, only how many of the
    hospital's measures in it were better or worse than the national rate, so
    the group is whichever of those is larger. None when the hospital reported
    no measures in the group.
    """
    if _int(raw.get(f"count_of_facility_{prefix}_measures")) == 0:
        return None
    better = _int(raw.get(f"count_of_{prefix}_measures_better"))
    worse = _int(raw.get(f"count_of_{prefix}_measures_worse"))
    if better > worse:
        return "above"
    if worse > better:
        return "below"
    return "same"


def transform_rating(raw: dict) -> dict:
    """The hospital_quality columns that come from the general information dataset."""
    try:
        stars = float(raw.get("hospital_overall_rating") or "")
    except ValueError:  # "Not Available"
        stars = None
    record = {"ccn": (raw.get("facility_id") or "").strip(), "overall_stars": stars}
    for column, prefix in GROUP_PREFIXES.items():
        record[column] = comparison_group(raw, prefix)
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

    places = PlaceIndex.load()
    all_records = []
    ratings = []
    offset = 0
    page_size = 500

    with RateLimitedClient() as client:
        while True:
            raw_results = fetch_hospitals(client, state=state, offset=offset, limit=page_size)
            if not raw_results:
                break

            for raw in raw_results:
                record = transform_hospital(raw, places)
                if record["ccn"] and record["name"]:
                    all_records.append(record)
                    ratings.append(transform_rating(raw))

            logger.info(f"Fetched {len(raw_results)} hospitals (offset={offset})")

            if len(raw_results) < page_size:
                break
            offset += page_size

    if not all_records:
        logger.warning("No hospitals found")
        return 0

    located = sum(1 for r in all_records if r["location"])
    logger.info(f"Geocoded {located} of {len(all_records)} hospitals by ZIP code")

    count = upsert_batch(
        table="hospitals",
        records=all_records,
        conflict_columns=["ccn"],
        update_columns=[c for c in all_records[0] if c != "ccn"] + ["updated_at"],
    )
    # Only the columns this dataset owns; the quality step fills the measure columns.
    upsert_batch(
        table="hospital_quality",
        records=ratings,
        conflict_columns=["ccn"],
        update_columns=["overall_stars", *GROUP_PREFIXES, "updated_at"],
    )

    added = add_postal_cities("hospitals", state)
    logger.info(f"Loaded {count} hospitals for state={state} ({added} postal city names added to ca_places)")
    return count


if __name__ == "__main__":
    run()
