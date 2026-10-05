import re

from fastapi import Query

from src.constants import CCN_PATTERN
from src.docs import CCNS_EXAMPLES
from src.exceptions import raise_validation_error
from src.prices.constants import MAX_COMPARE_HOSPITALS


def valid_ccn_list(
    ccns: str = Query(..., description="Comma-separated hospital CCNs (up to 10)", openapi_examples=CCNS_EXAMPLES),
) -> list[str]:
    """Parse and validate the comma-separated `ccns` parameter."""
    ccn_list = [c.strip() for c in ccns.split(",") if c.strip()]
    if not ccn_list:
        raise_validation_error("ccns", "At least one hospital CCN is required", ccns)
    if len(ccn_list) > MAX_COMPARE_HOSPITALS:
        raise_validation_error("ccns", f"At most {MAX_COMPARE_HOSPITALS} hospitals can be compared", ccns)
    bad = [c for c in ccn_list if not re.fullmatch(CCN_PATTERN, c)]
    if bad:
        raise_validation_error("ccns", f"Not a valid 6-character CCN: {', '.join(bad)}", ccns)
    return ccn_list
