from fastapi import Depends, Path
from psycopg import AsyncConnection

from src.constants import CCN_PATTERN
from src.database import get_db
from src.docs import CCN_EXAMPLES
from src.hospitals import service
from src.hospitals.exceptions import HospitalNotFound


async def valid_hospital_ccn(
    ccn: str = Path(..., pattern=CCN_PATTERN, description="CMS Certification Number", openapi_examples=CCN_EXAMPLES),
    conn: AsyncConnection = Depends(get_db),
) -> dict:
    """The hospital for `ccn`, or 404. Shared by every /hospitals/{ccn}... route."""
    hospital = await service.get_hospital(conn, ccn)
    if hospital is None:
        raise HospitalNotFound()
    return hospital
