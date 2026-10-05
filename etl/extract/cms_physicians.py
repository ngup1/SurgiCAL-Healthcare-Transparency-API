"""
NPPES Physician/Provider Extractor

Loads surgical providers from the NPPES (National Plan and Provider Enumeration System)
registry API and the CMS Physician Compare dataset.

Sources:
- NPPES API: https://npiregistry.cms.hhs.gov/api/
- CMS Physician Compare: data.cms.gov

Populates the `providers` and `provider_affiliations` tables.
"""
from __future__ import annotations


from etl.common.config import get_logger
from etl.common.db import upsert_batch, execute_query
from etl.common.http import fetch_json, RateLimitedClient

logger = get_logger(__name__)

NPPES_API_URL = "https://npiregistry.cms.hhs.gov/api/?version=2.1"

# NUCC taxonomy codes for surgical specialties
SURGICAL_TAXONOMIES = {
    "207X00000X": "Orthopedic Surgery",
    "207XS0114X": "Adult Reconstructive Orthopaedic Surgery",
    "207XX0004X": "Orthopedic Surgery of the Spine",
    "207XS0106X": "Hand Surgery (Orthopedic)",
    "2086S0102X": "Surgical Critical Care",
    "2086S0120X": "Pediatric Surgery",
    "2086S0122X": "Plastic and Reconstructive Surgery",
    "2086S0127X": "Trauma Surgery",
    "2086S0129X": "Vascular Surgery",
    "208G00000X": "Thoracic Surgery",
    "208C00000X": "Colon and Rectal Surgery",
    "2085R0202X": "Diagnostic Radiology",
    "207RC0200X": "Critical Care Medicine",
    "207RG0100X": "Gastroenterology",
    "207RE0101X": "Endocrinology",
    "207RN0300X": "Nephrology",
    "207RI0200X": "Infectious Disease",
    "207RC0000X": "Cardiovascular Disease",
    "204C00000X": "Neuromusculoskeletal Medicine",
    "207T00000X": "Neurological Surgery",
    "207UN0903X": "Urology - Female Pelvic Medicine",
    "207U00000X": "Urology",
}


def search_nppes(
    taxonomy: str,
    state: str = "CA",
    skip: int = 0,
    limit: int = 200,
) -> list[dict]:
    """
    Search the NPPES registry API for providers by taxonomy and state.

    The NPPES API returns max 200 results per call and supports skip-based pagination.
    """
    params = {
        "version": "2.1",
        "taxonomy_description": "",
        "state": state,
        "limit": limit,
        "skip": skip,
        "enumeration_type": "NPI-1",  # Individual providers only
    }

    # The API accepts taxonomy_description (partial match) rather than exact code.
    # We use the taxonomy code in the results to filter.
    specialty_name = SURGICAL_TAXONOMIES.get(taxonomy, "")
    if specialty_name:
        params["taxonomy_description"] = specialty_name

    data = fetch_json(NPPES_API_URL, params=params)
    return data.get("results", [])


def transform_provider(npi_record: dict) -> dict | None:
    """Transform an NPPES record into a providers table row."""
    basic = npi_record.get("basic", {})
    npi = str(npi_record.get("number", "")).strip()
    if not npi:
        return None

    first_name = basic.get("first_name", "").strip()
    last_name = basic.get("last_name", "").strip()
    if not first_name or not last_name:
        return None

    credential = basic.get("credential", "").strip() or None
    gender = basic.get("gender", "").strip() or None

    # Extract primary taxonomy
    taxonomy_code = None
    specialty = None
    for tax in npi_record.get("taxonomies", []):
        code = tax.get("code", "")
        if code in SURGICAL_TAXONOMIES:
            taxonomy_code = code
            specialty = SURGICAL_TAXONOMIES[code]
            break
        if tax.get("primary", False):
            taxonomy_code = code
            specialty = (tax.get("desc") or "").strip() or None

    # Extract primary practice address
    city = None
    state = None
    lat = None
    lng = None
    for addr in npi_record.get("addresses", []):
        if addr.get("address_purpose") == "LOCATION":
            city = addr.get("city", "").strip()
            state = addr.get("state", "").strip()
            break

    record = {
        "npi": npi,
        "first_name": first_name,
        "last_name": last_name,
        "credential": credential,
        "specialty": specialty,
        "taxonomy_code": taxonomy_code,
        "gender": gender,
        "medical_school": None,  # Not in NPPES; enriched via Trilliant
        "graduation_year": None,
        "city": city,
        "state": state,
        "location": None,  # Geocoded separately if needed
    }
    return record


def extract_affiliations(npi: str, npi_record: dict, ca_ccns: set[str]) -> list[dict]:
    """
    Extract hospital affiliations from an NPPES record.

    The NPPES record has practice location addresses but not direct CCN links.
    We match based on the 'other_names' or practiceLocations if available.
    For MVP, affiliations will be enriched via Trilliant or CMS Physician Compare.
    """
    # The NPPES API doesn't directly provide CCN affiliations.
    # We'll rely on CMS Physician Compare or Trilliant for this.
    return []


def run(state: str = "CA") -> int:
    """
    Run the NPPES physician extractor for surgical specialties.

    Iterates through all surgical taxonomy codes, paginating through results.

    Returns:
        Number of providers loaded.
    """
    logger.info(f"Starting NPPES physician extraction for state={state}")

    all_providers: dict[str, dict] = {}

    for taxonomy_code, specialty_name in SURGICAL_TAXONOMIES.items():
        logger.info(f"Searching taxonomy: {specialty_name} ({taxonomy_code})")
        skip = 0
        max_skip = 1800  # safety cap (~900 providers per taxonomy)
        while skip <= max_skip:
            results = search_nppes(
                taxonomy=taxonomy_code, state=state, skip=skip
            )
            if not results:
                break

            for raw in results:
                provider = transform_provider(raw)
                if provider and provider["npi"] not in all_providers:
                    all_providers[provider["npi"]] = provider

            logger.debug(f"  Fetched {len(results)} results (skip={skip})")
            if len(results) < 200:
                break
            skip += 200

    records = list(all_providers.values())
    if not records:
        logger.warning("No providers found")
        return 0

    count = upsert_batch(
        table="providers",
        records=records,
        conflict_columns=["npi"],
    )

    logger.info(f"Loaded {count} providers for state={state}")
    return count


if __name__ == "__main__":
    run()
