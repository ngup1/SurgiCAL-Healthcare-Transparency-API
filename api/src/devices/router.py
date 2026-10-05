"""Device endpoints: list, detail, recalls, adverse events, by-procedure."""

from fastapi import APIRouter, Depends, Path, Query, Response
from psycopg import AsyncConnection

from src.constants import CPT_PATTERN, PRODUCT_CODE_PATTERN
from src.database import get_db
from src.devices import service
from src.devices.constants import EventType
from src.devices.dependencies import valid_device_id
from src.devices.schemas import AdverseEvent, DeviceDetail, DeviceForProcedure, DeviceSummary, Recall
from src.docs import CPT_EXAMPLES, not_found
from src.pagination import PAGINATED_RESPONSES, Pagination, pagination, set_total_count

router = APIRouter()

DEVICE_NOT_FOUND = not_found("No device has this ID")


@router.get("", summary="List devices", response_model=list[DeviceSummary], responses=PAGINATED_RESPONSES)
async def list_devices(
    response: Response,
    product_code: str | None = Query(None, pattern=PRODUCT_CODE_PATTERN, description="3-letter FDA product code"),
    manufacturer: str | None = Query(None, description="Partial match, e.g. `meridian`"),
    medical_specialty: str | None = Query(None, description="Partial match, e.g. `ortho`, `cardio`"),
    q: str | None = Query(
        None, min_length=2, max_length=100, description="Fuzzy brand-name search, e.g. `knee` or `pacemaker`"
    ),
    page: Pagination = Depends(pagination),
    conn: AsyncConnection = Depends(get_db),
):
    """Medical devices, sorted by brand (or by relevance when `q` is given)."""
    rows, total = await service.list_devices(
        conn, product_code=product_code, manufacturer=manufacturer, medical_specialty=medical_specialty, q=q, page=page
    )
    set_total_count(response, total)
    return rows


@router.get("/by-procedure/{cpt}", summary="Devices used in a procedure", response_model=list[DeviceForProcedure])
async def devices_by_procedure(
    cpt: str = Path(..., pattern=CPT_PATTERN, description="CPT procedure code", openapi_examples=CPT_EXAMPLES),
    conn: AsyncConnection = Depends(get_db),
):
    """Implants, instruments, and consumables used in a procedure."""
    return await service.devices_by_procedure(conn, cpt)


@router.get(
    "/{device_id}",
    summary="Get a device with its safety record",
    response_model=DeviceDetail,
    responses=DEVICE_NOT_FOUND,
)
async def get_device(device: dict = Depends(valid_device_id), conn: AsyncConnection = Depends(get_db)):
    """Device details, its 10 most recent recalls, and adverse-event counts by type."""
    return {
        **device,
        "recent_recalls": await service.recent_recalls(conn, device["id"]),
        "adverse_event_summary": await service.adverse_event_summary(conn, device["id"]),
    }


@router.get(
    "/{device_id}/recalls",
    summary="List recalls for a device",
    response_model=list[Recall],
    responses={**DEVICE_NOT_FOUND, **PAGINATED_RESPONSES},
)
async def device_recalls(
    response: Response,
    device: dict = Depends(valid_device_id),
    page: Pagination = Depends(pagination),
    conn: AsyncConnection = Depends(get_db),
):
    """FDA recalls for the device, newest first."""
    rows, total = await service.list_recalls(conn, device["id"], page)
    set_total_count(response, total)
    return rows


@router.get(
    "/{device_id}/adverse-events",
    summary="List adverse events for a device",
    response_model=list[AdverseEvent],
    responses={**DEVICE_NOT_FOUND, **PAGINATED_RESPONSES},
)
async def device_adverse_events(
    response: Response,
    device: dict = Depends(valid_device_id),
    event_type: EventType | None = Query(None, description="Only this kind of report"),
    page: Pagination = Depends(pagination),
    conn: AsyncConnection = Depends(get_db),
):
    """FDA MAUDE adverse-event reports for the device, newest first."""
    rows, total = await service.list_adverse_events(conn, device["id"], event_type=event_type, page=page)
    set_total_count(response, total)
    return rows
