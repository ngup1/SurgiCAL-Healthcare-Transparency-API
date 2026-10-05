"""Device marketplace endpoints: list, detail, recalls, adverse events, by-procedure."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from psycopg2.extras import RealDictCursor

from api.dependencies import get_db
from api.docs import CPT_EXAMPLES, DEVICE_ID_EXAMPLES, not_found
from api.validation import CPT_PATTERN, PRODUCT_CODE_PATTERN

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
    where_parts = []
    params: list = []

    if product_code:
        where_parts.append("fda_product_code = %s")
        params.append(product_code)
    if manufacturer:
        where_parts.append("manufacturer ILIKE %s")
        params.append(f"%{manufacturer}%")
    if medical_specialty:
        where_parts.append("medical_specialty ILIKE %s")
        params.append(f"%{medical_specialty}%")
    if q:
        where_parts.append("%s <%% brand_name")  # trigram word similarity
        params.append(q)

    where_sql = " AND ".join(where_parts) if where_parts else "TRUE"
    order = "word_similarity(%s, brand_name) DESC, brand_name ASC" if q else "brand_name ASC"
    if q:
        params.append(q)

    sql = f"""
        SELECT id, fda_product_code, brand_name, generic_name,
               manufacturer, device_class, medical_specialty, premarket_number
        FROM devices
        WHERE {where_sql}
        ORDER BY {order}
        LIMIT %s OFFSET %s
    """
    params.extend([limit, offset])

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


@router.get("/by-procedure/{cpt}", summary="Devices used in a procedure")
def devices_by_procedure(
    cpt: str = Path(..., pattern=CPT_PATTERN, description="CPT procedure code", openapi_examples=CPT_EXAMPLES),
    conn=Depends(get_db),
):
    """Implants, instruments, and consumables used in a procedure."""
    sql = """
        SELECT d.id, d.fda_product_code, d.brand_name, d.generic_name,
               d.manufacturer, d.device_class, d.medical_specialty,
               dpm.usage_type
        FROM device_procedure_map dpm
        JOIN devices d ON dpm.device_id = d.id
        WHERE dpm.cpt = %s
        ORDER BY d.brand_name
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, (cpt,))
        return cur.fetchall()


@router.get("/{device_id}", summary="Get a device with its safety record", responses=not_found("No device has this ID"))
def get_device(
    device_id: UUID = Path(..., openapi_examples=DEVICE_ID_EXAMPLES),
    conn=Depends(get_db),
):
    """Device details, its 10 most recent recalls, and adverse-event counts by type."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Device info
        cur.execute(
            """
            SELECT id, fda_product_code, brand_name, generic_name,
                   manufacturer, device_class, medical_specialty,
                   premarket_number, description
            FROM devices WHERE id = %s
            """,
            (str(device_id),),
        )
        device = cur.fetchone()
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        # Recent recalls
        cur.execute(
            """
            SELECT recall_number, recall_class, reason, status,
                   recall_date, termination_date
            FROM device_recalls
            WHERE device_id = %s
            ORDER BY recall_date DESC NULLS LAST
            LIMIT 10
            """,
            (str(device_id),),
        )
        recalls = cur.fetchall()

        # Adverse event summary
        cur.execute(
            """
            SELECT event_type, COUNT(*) AS count
            FROM device_adverse_events
            WHERE device_id = %s
            GROUP BY event_type
            ORDER BY count DESC
            """,
            (str(device_id),),
        )
        event_summary = cur.fetchall()

    result = dict(device)
    result["recent_recalls"] = [dict(r) for r in recalls]
    result["adverse_event_summary"] = [dict(e) for e in event_summary]
    return result


@router.get("/{device_id}/recalls", summary="List recalls for a device")
def device_recalls(
    device_id: UUID = Path(..., openapi_examples=DEVICE_ID_EXAMPLES),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn=Depends(get_db),
):
    """FDA recalls for the device, newest first."""
    sql = """
        SELECT recall_number, product_code, brand_name, manufacturer,
               recall_class, reason, status, recall_date, termination_date,
               quantity, distribution
        FROM device_recalls
        WHERE device_id = %s
        ORDER BY recall_date DESC NULLS LAST
        LIMIT %s OFFSET %s
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, (str(device_id), limit, offset))
        return cur.fetchall()


@router.get("/{device_id}/adverse-events", summary="List adverse events for a device")
def device_adverse_events(
    device_id: UUID = Path(..., openapi_examples=DEVICE_ID_EXAMPLES),
    event_type: Literal["death", "injury", "malfunction"] | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn=Depends(get_db),
):
    """FDA MAUDE adverse-event reports for the device, newest first."""
    where_parts = ["device_id = %s"]
    params: list = [str(device_id)]

    if event_type:
        where_parts.append("event_type = %s")
        params.append(event_type)

    where_sql = " AND ".join(where_parts)

    sql = f"""
        SELECT mdr_report_key, product_code, brand_name, manufacturer,
               event_type, event_date, patient_outcomes, device_problems,
               event_narrative
        FROM device_adverse_events
        WHERE {where_sql}
        ORDER BY event_date DESC NULLS LAST
        LIMIT %s OFFSET %s
    """
    params.extend([limit, offset])

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        return cur.fetchall()
