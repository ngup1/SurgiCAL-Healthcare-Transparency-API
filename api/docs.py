"""OpenAPI / Swagger UI content: descriptions, tag metadata, and example values.

Example values point at rows in the seed data, so "Try it out" → "Execute" works on
the first click. Only required and path parameters get examples: Swagger UI fills
examples into the form, and a pre-filled optional filter would silently narrow results.
"""

from typing import Any

from api.exceptions import NotFoundResponse, ValidationErrorResponse

API_DESCRIPTION = """

Compare what a procedure costs across hospitals and insurers, look up hospital and
surgeon quality, and check medical devices for recalls and adverse events.

**Trying it out:** every endpoint below is ready to run. Expand one and click
**Execute**. Required fields come pre-filled with working example values.

**Errors:** malformed input returns `422` naming each bad field; an unknown ID returns `404`.
""".strip()

TAGS_METADATA = [
    {
        "name": "search",
        "description": "Search procedures, providers, hospitals, and devices at once. "
        "Matching is fuzzy and works on single words, e.g. `knee` or `bayshore`.",
    },
    {
        "name": "prices",
        "description": "Negotiated rates, cash prices, and Medicare rates for a procedure (CPT code), "
        "cheapest first, with each hospital's quality rating alongside.",
    },
    {
        "name": "hospitals",
        "description": "Hospitals with CMS quality measures: star rating, mortality, safety, "
        "readmissions, and procedure-specific outcomes.",
    },
    {
        "name": "providers",
        "description": "Surgeons and physicians: specialty, volume, patient ratings, and hospital affiliations.",
    },
    {
        "name": "devices",
        "description": "Medical devices with FDA recalls and adverse-event (MAUDE) reports, "
        "and which devices are used in each procedure.",
    },
    {"name": "health", "description": "Service status and the deployed commit."},
]


def examples(*pairs: tuple[Any, str]) -> dict[str, dict[str, Any]]:
    """openapi_examples from (value, summary) pairs; the first one is pre-filled."""
    return {summary: {"summary": summary, "value": value} for value, summary in pairs}


CCN_EXAMPLES = examples(
    ("050801", "Bayshore Regional Medical Center (San Francisco)"),
    ("050812", "Angel City Medical Center (Los Angeles)"),
)
NPI_EXAMPLES = examples(("1572628497", "Daniel Castellanos, orthopaedic surgery"))
CPT_EXAMPLES = examples(
    ("27447", "Total knee replacement"),
    ("27130", "Total hip replacement"),
    ("66984", "Cataract surgery"),
)
CCNS_EXAMPLES = examples(("050801,050802,050803", "Three San Francisco Bay Area hospitals"))
DEVICE_ID_EXAMPLES = examples(
    ("68670191-dbe2-5dbd-a693-5f7118cab2c2", "Cardiovance Pulse DR Pacemaker"),
)
SEARCH_EXAMPLES = examples(
    ("knee", "Procedures and devices"),
    ("bayshore", "A hospital"),
    ("castellanos", "A provider"),
)

# Documented on every router, replacing FastAPI's default 422 schema with the
# shape api/exceptions.py actually returns.
VALIDATION_RESPONSES: dict[int | str, dict[str, Any]] = {
    422: {"model": ValidationErrorResponse, "description": "Malformed input; each bad field is named"},
}


def not_found(description: str) -> dict[int | str, dict[str, Any]]:
    return {404: {"model": NotFoundResponse, "description": description}}
