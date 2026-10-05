"""Hospital endpoints: list, detail, affiliated providers."""

from fastapi import APIRouter, Depends, Path, Query

from src.constants import CCN_PATTERN, STATE_PATTERN
from src.database import get_db
from src.docs import CCN_EXAMPLES, not_found
from src.geo import require_lat_lng_pair
from src.hospitals import service
from src.hospitals.exceptions import HospitalNotFound

router = APIRouter()


@router.get("", summary="List hospitals")
async def list_hospitals(
    state: str = Query("CA", pattern=STATE_PATTERN, description="Two-letter state code"),
    lat: float | None = Query(None, ge=-90, le=90),
    lng: float | None = Query(None, ge=-180, le=180),
    radius_miles: float = Query(25, gt=0, le=250),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn=Depends(get_db),
):
    """
    List hospitals, sorted by name.

    To search near a point, pass `lat` and `lng` together (e.g. `34.0522`, `-118.2437` for
    downtown Los Angeles): results are then limited to `radius_miles` and sorted nearest first.
    """
    require_lat_lng_pair(lat, lng)
    return await service.list_hospitals(
        conn, state=state, lat=lat, lng=lng, radius_miles=radius_miles, limit=limit, offset=offset
    )


@router.get("/{ccn}", summary="Get a hospital with quality measures", responses=not_found("No hospital has this CCN"))
async def get_hospital(
    ccn: str = Path(..., pattern=CCN_PATTERN, description="CMS Certification Number", openapi_examples=CCN_EXAMPLES),
    conn=Depends(get_db),
):
    """Hospital details and CMS quality measures: star rating, comparison groups, and procedure-specific rates."""
    hospital = await service.get_hospital(conn, ccn)
    if hospital is None:
        raise HospitalNotFound()
    return hospital


@router.get("/{ccn}/providers", summary="List providers at a hospital")
async def list_hospital_providers(
    ccn: str = Path(..., pattern=CCN_PATTERN, description="CMS Certification Number", openapi_examples=CCN_EXAMPLES),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn=Depends(get_db),
):
    """Providers affiliated with the hospital, highest-volume first."""
    return await service.list_hospital_providers(conn, ccn, limit=limit, offset=offset)
