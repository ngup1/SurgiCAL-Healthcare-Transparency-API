"""Pricing endpoints: search by CPT/location/payer, compare hospitals."""

from fastapi import APIRouter, Depends, Query, Response
from psycopg import AsyncConnection

from src.constants import CPT_PATTERN
from src.database import get_db
from src.docs import CPT_EXAMPLES
from src.locations.dependencies import valid_location
from src.locations.schemas import LocationFilter
from src.pagination import PAGINATED_RESPONSES, Pagination, pagination, set_total_count
from src.prices import service
from src.prices.dependencies import valid_ccn_list
from src.prices.schemas import PriceComparisonRow, PriceRow

router = APIRouter()


@router.get("", summary="Prices for a procedure", response_model=list[PriceRow], responses=PAGINATED_RESPONSES)
async def search_prices(
    response: Response,
    cpt: str = Query(..., pattern=CPT_PATTERN, description="CPT procedure code", openapi_examples=CPT_EXAMPLES),
    payer: str | None = Query(None, description="Partial match on insurer, e.g. `aetna`, `medicare`, `cash`"),
    location: LocationFilter = Depends(valid_location),
    page: Pagination = Depends(pagination),
    conn: AsyncConnection = Depends(get_db),
):
    """
    Every price for a procedure, cheapest negotiated rate first: one row per hospital, insurer,
    and plan, with the hospital's cash price, rate range, and quality rating.

    Filter hospitals by `city`, `county`, or `zip` (add `radius_miles` to include nearby places).
    """
    rows, total = await service.search_prices(conn, cpt=cpt, payer=payer, location=location, page=page)
    set_total_count(response, total)
    return rows


@router.get("/compare", summary="Compare a procedure across hospitals", response_model=list[PriceComparisonRow])
async def compare_prices(
    cpt: str = Query(..., pattern=CPT_PATTERN, description="CPT procedure code", openapi_examples=CPT_EXAMPLES),
    ccns: list[str] = Depends(valid_ccn_list),
    conn: AsyncConnection = Depends(get_db),
):
    """Side-by-side prices for one procedure at the hospitals you choose, grouped by hospital and insurer."""
    return await service.compare_prices(conn, cpt=cpt, ccns=ccns)
