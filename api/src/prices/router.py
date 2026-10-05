"""Pricing endpoints: search by CPT/location/payer, compare hospitals."""

from fastapi import APIRouter, Depends, Query

from src.constants import CPT_PATTERN
from src.database import get_db
from src.docs import CPT_EXAMPLES
from src.geo import require_lat_lng_pair
from src.prices import service
from src.prices.dependencies import valid_ccn_list

router = APIRouter()


@router.get("", summary="Prices for a procedure")
def search_prices(
    cpt: str = Query(..., pattern=CPT_PATTERN, description="CPT procedure code", openapi_examples=CPT_EXAMPLES),
    lat: float | None = Query(None, ge=-90, le=90),
    lng: float | None = Query(None, ge=-180, le=180),
    radius_miles: float = Query(50, gt=0, le=250),
    payer: str | None = Query(None, description="Partial match on insurer, e.g. `aetna`, `medicare`, `cash`"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn=Depends(get_db),
):
    """
    Every price for a procedure, cheapest negotiated rate first: one row per hospital, insurer,
    and plan, with the hospital's cash price, rate range, and quality rating.

    Pass `lat` and `lng` together to limit to hospitals within `radius_miles`.
    """
    require_lat_lng_pair(lat, lng)
    return service.search_prices(
        conn, cpt=cpt, lat=lat, lng=lng, radius_miles=radius_miles, payer=payer, limit=limit, offset=offset
    )


@router.get("/compare", summary="Compare a procedure across hospitals")
def compare_prices(
    cpt: str = Query(..., pattern=CPT_PATTERN, description="CPT procedure code", openapi_examples=CPT_EXAMPLES),
    ccns: list[str] = Depends(valid_ccn_list),
    conn=Depends(get_db),
):
    """Side-by-side prices for one procedure at the hospitals you choose, grouped by hospital and insurer."""
    return service.compare_prices(conn, cpt=cpt, ccns=ccns)
