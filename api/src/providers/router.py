"""Provider/surgeon endpoints: list, detail with metrics and affiliations."""

from fastapi import APIRouter, Depends, Path, Query

from src.constants import NPI_PATTERN, STATE_PATTERN
from src.database import get_db
from src.docs import NPI_EXAMPLES, not_found
from src.geo import require_lat_lng_pair
from src.providers import service
from src.providers.exceptions import ProviderNotFound

router = APIRouter()


@router.get("", summary="List providers")
def list_providers(
    specialty: str | None = Query(None, description="Partial match, e.g. `ortho` or `cardio`"),
    state: str = Query("CA", pattern=STATE_PATTERN, description="Two-letter state code"),
    city: str | None = Query(None, description="Partial match, e.g. `san` or `los angeles`"),
    lat: float | None = Query(None, ge=-90, le=90),
    lng: float | None = Query(None, ge=-180, le=180),
    radius_miles: float = Query(25, gt=0, le=250),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn=Depends(get_db),
):
    """
    List providers, sorted by last name. Filter by specialty and city, or pass `lat` and
    `lng` together to search within `radius_miles` (nearest first).
    """
    require_lat_lng_pair(lat, lng)
    return service.list_providers(
        conn,
        specialty=specialty,
        state=state,
        city=city,
        lat=lat,
        lng=lng,
        radius_miles=radius_miles,
        limit=limit,
        offset=offset,
    )


@router.get("/{npi}", summary="Get a provider", responses=not_found("No provider has this NPI"))
def get_provider(
    npi: str = Path(
        ..., pattern=NPI_PATTERN, description="National Provider Identifier", openapi_examples=NPI_EXAMPLES
    ),
    conn=Depends(get_db),
):
    """Provider details, volume and rating metrics, and hospital affiliations (primary first)."""
    provider = service.get_provider(conn, npi)
    if provider is None:
        raise ProviderNotFound()
    return {**provider, "affiliations": service.list_affiliations(conn, npi)}
