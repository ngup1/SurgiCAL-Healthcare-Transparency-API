import re

from fastapi import Query

from src.constants import CCN_PATTERN
from src.docs import CCNS_EXAMPLES
from src.exceptions import raise_validation_error
from src.prices.constants import MAX_COMPARE_HOSPITALS


async def valid_ccn_list(
    ccns: list[str] = Query(
        ...,
        description=f"Hospital CCNs, up to {MAX_COMPARE_HOSPITALS}: repeat the parameter "
        "(`ccns=050801&ccns=050802`) or separate with commas",
        openapi_examples=CCNS_EXAMPLES,
    ),
) -> list[str]:
    """Flatten repeated and comma-separated `ccns`, then validate them."""
    ccn_list = [c.strip() for value in ccns for c in value.split(",") if c.strip()]
    raw = ",".join(ccns)
    if not ccn_list:
        raise_validation_error("ccns", "At least one hospital CCN is required", raw)
    if len(ccn_list) > MAX_COMPARE_HOSPITALS:
        raise_validation_error("ccns", f"At most {MAX_COMPARE_HOSPITALS} hospitals can be compared", raw)
    bad = [c for c in ccn_list if not re.fullmatch(CCN_PATTERN, c)]
    if bad:
        raise_validation_error("ccns", f"Not a valid 6-character CCN: {', '.join(bad)}", raw)
    return list(dict.fromkeys(ccn_list))  # de-duplicate, keep order
