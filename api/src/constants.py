"""Identifier formats shared across domains.

Format rules live on the route parameters (Query/Path `pattern`), so FastAPI rejects
bad input with a 422 before any SQL runs.
"""

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
