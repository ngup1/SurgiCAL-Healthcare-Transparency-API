"""Provider/surgeon endpoints: list, detail with metrics and affiliations."""

from fastapi import APIRouter, Depends, Query, Response
from psycopg import AsyncConnection

from src.database import get_db
from src.docs import not_found
from src.locations.dependencies import valid_location
from src.locations.schemas import LocationFilter
from src.pagination import PAGINATED_RESPONSES, Pagination, pagination, set_total_count
from src.providers import service
from src.providers.dependencies import valid_provider_npi
from src.providers.schemas import ProviderDetail, ProviderSummary

router = APIRouter()


@router.get("", summary="List providers", response_model=list[ProviderSummary], responses=PAGINATED_RESPONSES)
async def list_providers(
    response: Response,
    specialty: str | None = Query(None, description="Partial match, e.g. `ortho` or `cardio`"),
    location: LocationFilter = Depends(valid_location),
    page: Pagination = Depends(pagination),
    conn: AsyncConnection = Depends(get_db),
):
    """
    Providers, sorted by last name. Filter by `specialty` and by `city`, `county`, or `zip`;
    add `radius_miles` to a city or ZIP search to include nearby places (sorted nearest first).
    """
    rows, total = await service.list_providers(conn, specialty=specialty, location=location, page=page)
    set_total_count(response, total)
    return rows


@router.get(
    "/{npi}", summary="Get a provider", response_model=ProviderDetail, responses=not_found("No provider has this NPI")
)
async def get_provider(provider: dict = Depends(valid_provider_npi), conn: AsyncConnection = Depends(get_db)):
    """Provider details, volume and rating metrics, and hospital affiliations (primary first)."""
    return {**provider, "affiliations": await service.list_affiliations(conn, provider["npi"])}
