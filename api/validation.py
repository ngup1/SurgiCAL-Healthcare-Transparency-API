"""Input formats and checks shared by the routers.

Format rules live on the route parameters (Query/Path pattern, ge/le), so FastAPI
rejects bad input with a 422 before any SQL runs. Checks that span more than one
parameter use `raise_validation_error`, which produces the same 422 format.
"""

from typing import Any

from fastapi.exceptions import RequestValidationError

# CMS Certification Number: 6 characters, e.g. 050801 (some units use a letter, e.g. 05S001).
CCN_PATTERN = r"^[0-9A-Z]{6}$"
# National Provider Identifier: 10 digits.
NPI_PATTERN = r"^\d{10}$"
# CPT code: 4 digits + digit or letter (Category II/III codes end in F/T, e.g. 0001T).
CPT_PATTERN = r"^\d{4}[0-9A-Z]$"
# FDA product code: 3 uppercase letters, e.g. JWH.
PRODUCT_CODE_PATTERN = r"^[A-Z]{3}$"

STATE_PATTERN = r"^[A-Z]{2}$"

# Readable replacements for "String should match pattern '...'" in 422 responses.
PATTERN_MESSAGES = {
    CCN_PATTERN: "Must be a 6-character CMS Certification Number, e.g. 050801",
    NPI_PATTERN: "Must be a 10-digit National Provider Identifier, e.g. 1572628497",
    CPT_PATTERN: "Must be a 5-character CPT code, e.g. 27447",
    PRODUCT_CODE_PATTERN: "Must be a 3-letter uppercase FDA product code, e.g. JWH",
    STATE_PATTERN: "Must be a 2-letter uppercase state code, e.g. CA",
}

MAX_COMPARE_HOSPITALS = 10


def raise_validation_error(field: str, message: str, value: Any, location: str = "query") -> None:
    """Raise a 422 for `field`, formatted like FastAPI's own validation errors."""
    raise RequestValidationError([{"type": "value_error", "loc": (location, field), "msg": message, "input": value}])


def require_lat_lng_pair(lat: float | None, lng: float | None) -> None:
    """lat and lng only make sense together; one without the other is rejected, not ignored."""
    if (lat is None) != (lng is None):
        missing = "lng" if lng is None else "lat"
        given = "lat" if missing == "lng" else "lng"
        raise_validation_error(missing, f"'{missing}' is required when '{given}' is given", None)
