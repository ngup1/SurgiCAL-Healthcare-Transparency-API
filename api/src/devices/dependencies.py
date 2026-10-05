from uuid import UUID

from fastapi import Depends, Path
from psycopg import AsyncConnection

from src.database import get_db
from src.devices import service
from src.devices.exceptions import DeviceNotFound
from src.docs import DEVICE_ID_EXAMPLES


async def valid_device_id(
    device_id: UUID = Path(..., openapi_examples=DEVICE_ID_EXAMPLES),
    conn: AsyncConnection = Depends(get_db),
) -> dict:
    """The device for `device_id`, or 404. Shared by every /devices/{device_id}... route."""
    device = await service.get_device(conn, device_id)
    if device is None:
        raise DeviceNotFound()
    return device
