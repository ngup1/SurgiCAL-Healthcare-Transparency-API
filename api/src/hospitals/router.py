"""Hospital endpoints: list, detail, affiliated providers."""

from fastapi import APIRouter, Depends, Response
from psycopg import AsyncConnection

from src.database import get_db
from src.docs import not_found
from src.hospitals import service
from src.hospitals.dependencies import valid_hospital_ccn
from src.hospitals.schemas import HospitalDetail, HospitalProvider, HospitalSummary
from src.locations.dependencies import valid_location
from src.locations.schemas import LocationFilter
from src.pagination import PAGINATED_RESPONSES, Pagination, pagination, set_total_count

router = APIRouter()


@router.get("", summary="List hospitals", response_model=list[HospitalSummary], responses=PAGINATED_RESPONSES)
async def list_hospitals(
    response: Response,
    location: LocationFilter = Depends(valid_location),
    page: Pagination = Depends(pagination),
    conn: AsyncConnection = Depends(get_db),
):
    """
    California hospitals, sorted by name. Filter by `city`, `county`, or `zip`; add
    `radius_miles` to a city or ZIP search to include nearby places (sorted nearest first).
    """
    rows, total = await service.list_hospitals(conn, location, page)
    set_total_count(response, total)
    return rows


@router.get(
    "/{ccn}",
    summary="Get a hospital with quality measures",
    response_model=HospitalDetail,
    responses=not_found("No hospital has this CCN"),
)
async def get_hospital(hospital: dict = Depends(valid_hospital_ccn)):
    """Hospital details and CMS quality measures: star rating, comparison groups, and procedure-specific rates."""
    return hospital


@router.get(
    "/{ccn}/providers",
    summary="List providers at a hospital",
    response_model=list[HospitalProvider],
    responses={**not_found("No hospital has this CCN"), **PAGINATED_RESPONSES},
)
async def list_hospital_providers(
    response: Response,
    hospital: dict = Depends(valid_hospital_ccn),
    page: Pagination = Depends(pagination),
    conn: AsyncConnection = Depends(get_db),
):
    """Providers affiliated with the hospital, highest-volume first."""
    rows, total = await service.list_hospital_providers(conn, hospital["ccn"], page)
    set_total_count(response, total)
    return rows
