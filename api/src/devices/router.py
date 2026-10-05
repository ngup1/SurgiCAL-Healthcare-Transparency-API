"""Device endpoints: list, detail, recalls, adverse events, by-procedure."""

from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query

from src.constants import CPT_PATTERN, PRODUCT_CODE_PATTERN
from src.database import get_db
from src.devices import service
from src.devices.constants import EventType
from src.devices.exceptions import DeviceNotFound
from src.docs import CPT_EXAMPLES, DEVICE_ID_EXAMPLES, not_found

router = APIRouter()


@router.get("", summary="List devices")
def list_devices(
    product_code: str | None = Query(None, pattern=PRODUCT_CODE_PATTERN, description="3-letter FDA product code"),
    manufacturer: str | None = Query(None, description="Partial match, e.g. `meridian`"),
    medical_specialty: str | None = Query(None, description="Partial match, e.g. `ortho`, `cardio`"),
    q: str | None = Query(
        None, min_length=2, max_length=100, description="Fuzzy brand-name search, e.g. `knee` or `pacemaker`"
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn=Depends(get_db),
):
    """Medical devices, sorted by brand (or by relevance when `q` is given)."""
    return service.list_devices(
        conn,
        product_code=product_code,
        manufacturer=manufacturer,
        medical_specialty=medical_specialty,
        q=q,
        limit=limit,
        offset=offset,
    )


@router.get("/by-procedure/{cpt}", summary="Devices used in a procedure")
def devices_by_procedure(
    cpt: str = Path(..., pattern=CPT_PATTERN, description="CPT procedure code", openapi_examples=CPT_EXAMPLES),
    conn=Depends(get_db),
):
    """Implants, instruments, and consumables used in a procedure."""
    return service.devices_by_procedure(conn, cpt)


@router.get("/{device_id}", summary="Get a device with its safety record", responses=not_found("No device has this ID"))
def get_device(
    device_id: UUID = Path(..., openapi_examples=DEVICE_ID_EXAMPLES),
    conn=Depends(get_db),
):
    """Device details, its 10 most recent recalls, and adverse-event counts by type."""
    device = service.get_device(conn, device_id)
    if device is None:
        raise DeviceNotFound()
    return {
        **device,
        "recent_recalls": service.recent_recalls(conn, device_id),
        "adverse_event_summary": service.adverse_event_summary(conn, device_id),
    }


@router.get("/{device_id}/recalls", summary="List recalls for a device")
def device_recalls(
    device_id: UUID = Path(..., openapi_examples=DEVICE_ID_EXAMPLES),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn=Depends(get_db),
):
    """FDA recalls for the device, newest first."""
    return service.list_recalls(conn, device_id, limit=limit, offset=offset)


@router.get("/{device_id}/adverse-events", summary="List adverse events for a device")
def device_adverse_events(
    device_id: UUID = Path(..., openapi_examples=DEVICE_ID_EXAMPLES),
    event_type: EventType | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn=Depends(get_db),
):
    """FDA MAUDE adverse-event reports for the device, newest first."""
    return service.list_adverse_events(conn, device_id, event_type=event_type, limit=limit, offset=offset)
