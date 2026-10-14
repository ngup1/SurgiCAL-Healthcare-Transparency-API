"""
NPPES Physician/Provider Extractor

Loads surgical providers from the NPPES (National Plan and Provider Enumeration System)
registry API.

Source: https://npiregistry.cms.hhs.gov/api/

Populates the `providers` table.
"""
from __future__ import annotations

from collections.abc import Iterator

from etl.common.config import get_logger
from etl.common.db import upsert_batch
from etl.common.http import RateLimitedClient
from etl.common.places import PlaceIndex, add_postal_cities

logger = get_logger(__name__)

NPPES_API_URL = "https://npiregistry.cms.hhs.gov/api/"

# Specialty names to search for. They must be NPPES's own spellings ("Orthopaedic",
# "Colon & Rectal"): a name it doesn't know returns an error, not an empty result.
# The search matches any taxonomy on a record and also returns nurses and assistants
# in the specialty, so results are filtered by the taxonomy codes below.
SEARCH_DESCRIPTIONS = [
    "Orthopaedic Surgery",
    "Surgery",
    "Thoracic Surgery",
    "Colon & Rectal Surgery",
    "Neurological Surgery",
    "Plastic Surgery",
    "Urology",
    "Obstetrics & Gynecology",
    "Ophthalmology",
    "Otolaryngology",
    "Transplant Surgery",
    "Oral & Maxillofacial Surgery",
    "Cardiovascular Disease",
    "Interventional Cardiology",
    "Gastroenterology",
    "Critical Care Medicine",
    "Neuromusculoskeletal Medicine",
]

# NUCC taxonomy code families (first 4 characters) for surgical specialties.
# A family covers the specialty and its subspecialties, e.g. 207X is orthopaedic
# surgery, including hand, spine, sports medicine and adult reconstructive.
SURGICAL_TAXONOMY_FAMILIES = {
    "207X": "Orthopaedic Surgery",
    "2086": "General Surgery",
    "208G": "Thoracic Surgery (Cardiothoracic Vascular Surgery)",
    "208C": "Colon & Rectal Surgery",
    "207T": "Neurological Surgery",
    "2082": "Plastic Surgery",
    "2088": "Urology",
    "207V": "Obstetrics & Gynecology",
    "207W": "Ophthalmology",
    "207Y": "Otolaryngology",
    "204F": "Transplant Surgery",
    "204E": "Oral & Maxillofacial Surgery",
    "204C": "Neuromusculoskeletal Medicine, Sports Medicine",
}
# Procedural specialties inside internal medicine, which is not surgical as a family.
SURGICAL_TAXONOMY_CODES = {
    "207RC0000X": "Cardiovascular Disease",
    "207RI0011X": "Interventional Cardiology",
    "207RG0100X": "Gastroenterology",
    "207RC0200X": "Critical Care Medicine",
}

PAGE_SIZE = 200
# NPPES ignores `skip` past 1000, so one search can return 1,200 records at most.
MAX_SKIP = 1000
MAX_PER_SEARCH = MAX_SKIP + PAGE_SIZE


class NppesError(Exception):
    """NPPES answered with an error body (it does so with HTTP 200)."""


def is_surgical(code: str) -> bool:
    return code in SURGICAL_TAXONOMY_CODES or code[:4] in SURGICAL_TAXONOMY_FAMILIES


def specialty_name(code: str, desc: str | None) -> str | None:
    """
    Specialty to store for a taxonomy: the subspecialty where NPPES gives one
    ('Orthopaedic Surgery, Hand Surgery' -> 'Orthopaedic Surgery, Hand Surgery'),
    otherwise our name for the code or family.
    """
    desc = (desc or "").strip()
    if code in SURGICAL_TAXONOMY_CODES:
        return SURGICAL_TAXONOMY_CODES[code]
    family = SURGICAL_TAXONOMY_FAMILIES.get(code[:4])
    if family and desc.startswith("Surgery"):
        # NPPES calls general surgery just "Surgery".
        return desc.replace("Surgery", family, 1)
    return desc or family


def search_page(
    client: RateLimitedClient,
    description: str,
    state: str,
    postal_prefix: str | None,
    skip: int,
) -> list[dict]:
    """One page of an NPPES search for individual providers in a state."""
    params = {
        "version": "2.1",
        "taxonomy_description": description,
        "state": state,
        "limit": PAGE_SIZE,
        "skip": skip,
        "enumeration_type": "NPI-1",  # Individual providers only
    }
    if postal_prefix:
        params["postal_code"] = f"{postal_prefix}*"

    data = client.get_json(NPPES_API_URL, params=params)
    if "Errors" in data:
        raise NppesError(f"NPPES rejected the search for {description!r}: {data['Errors']}")
    return data["results"]


def search_all(
    client: RateLimitedClient,
    description: str,
    state: str,
    zip_prefixes: list[str],
    postal_prefix: str | None = None,
) -> Iterator[dict]:
    """
    Every record for a specialty search.

    A search that fills all 1,200 slots was cut off, so it is repeated once per
    longer ZIP prefix (the state's 2-digit prefixes first, then one more digit
    at a time) until each piece fits.
    """
    results: list[dict] = []
    for skip in range(0, MAX_SKIP + 1, PAGE_SIZE):
        page = search_page(client, description, state, postal_prefix, skip)
        results.extend(page)
        if len(page) < PAGE_SIZE:
            break

    if len(results) < MAX_PER_SEARCH:
        yield from results
        return

    if postal_prefix is None:
        narrower = zip_prefixes
    elif len(postal_prefix) < 5:
        narrower = [postal_prefix + d for d in "0123456789"]
    else:
        logger.warning(f"{description} in ZIP {postal_prefix}: more than {MAX_PER_SEARCH} providers; some are missed")
        yield from results
        return

    logger.info(f"  {description} ({postal_prefix or state}): over {MAX_PER_SEARCH} results, splitting by ZIP")
    for prefix in narrower:
        yield from search_all(client, description, state, zip_prefixes, prefix)


def transform_provider(npi_record: dict, state: str, places: PlaceIndex) -> dict | None:
    """
    Transform an NPPES record into a providers table row.

    Returns None for records without a surgical taxonomy or without a practice
    location in the state.
    """
    basic = npi_record.get("basic", {})
    npi = str(npi_record.get("number", "")).strip()
    if not npi:
        return None

    first_name = (basic.get("first_name") or "").strip()
    last_name = (basic.get("last_name") or "").strip()
    if not first_name or not last_name:
        return None

    # The primary taxonomy if it is surgical, otherwise the first surgical one.
    surgical = [t for t in npi_record.get("taxonomies", []) if is_surgical(t.get("code") or "")]
    if not surgical:
        return None
    taxonomy = next((t for t in surgical if t.get("primary")), surgical[0])

    # Primary practice address
    location = next(
        (a for a in npi_record.get("addresses", []) if a.get("address_purpose") == "LOCATION"),
        None,
    )
    if location is None or (location.get("state") or "").strip() != state:
        return None
    city = places.city_name(location.get("city"))
    zip_code = (location.get("postal_code") or "").strip()[:5]

    # medical_school and graduation_year are not in NPPES and are left out on
    # purpose, so a refresh keeps what enrichment wrote.
    return {
        "npi": npi,
        "first_name": first_name.title(),
        "last_name": last_name.title(),
        "credential": (basic.get("credential") or "").strip() or None,
        "specialty": specialty_name(taxonomy["code"], taxonomy.get("desc")),
        "taxonomy_code": taxonomy["code"],
        "gender": (basic.get("sex") or "").strip() or None,
        "city": city,
        "state": state,
        "location": places.locate(zip_code, city),
    }


def run(state: str = "CA") -> int:
    """
    Run the NPPES physician extractor for surgical specialties.

    Returns:
        Number of providers loaded.
    """
    logger.info(f"Starting NPPES physician extraction for state={state}")

    places = PlaceIndex.load()
    zip_prefixes = sorted({z[:2] for z in places.zips})
    all_providers: dict[str, dict] = {}

    with RateLimitedClient() as client:
        for description in SEARCH_DESCRIPTIONS:
            seen = 0
            for raw in search_all(client, description, state, zip_prefixes):
                seen += 1
                provider = transform_provider(raw, state, places)
                if provider:
                    all_providers.setdefault(provider["npi"], provider)
            logger.info(f"Searched {description}: {seen} records, {len(all_providers)} surgical providers so far")

    records = list(all_providers.values())
    if not records:
        logger.warning("No providers found")
        return 0

    located = sum(1 for r in records if r["location"])
    logger.info(f"Geocoded {located} of {len(records)} providers by ZIP code")

    count = upsert_batch(
        table="providers",
        records=records,
        conflict_columns=["npi"],
        update_columns=[c for c in records[0] if c != "npi"] + ["updated_at"],
    )

    # At least 5 providers must use a city name before it becomes a place.
    added = add_postal_cities("providers", state, min_rows=5)
    logger.info(f"Loaded {count} providers for state={state} ({added} postal city names added to ca_places)")
    return count


if __name__ == "__main__":
    run()
