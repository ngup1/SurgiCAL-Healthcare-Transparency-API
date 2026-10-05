"""Device, recall, and adverse-event queries."""

from uuid import UUID

from psycopg import AsyncConnection

from src.database import fetch_all, fetch_one
from src.pagination import Pagination, paginate


async def list_devices(
    conn: AsyncConnection,
    *,
    product_code: str | None,
    manufacturer: str | None,
    medical_specialty: str | None,
    q: str | None,
    page: Pagination,
) -> tuple[list[dict], int]:
    where: list[str] = []
    params: list = []
    if product_code:
        where.append("fda_product_code = %s")
        params.append(product_code)
    if manufacturer:
        where.append("manufacturer ILIKE %s")
        params.append(f"%{manufacturer}%")
    if medical_specialty:
        where.append("medical_specialty ILIKE %s")
        params.append(f"%{medical_specialty}%")
    if q:
        where.append("%s <%% brand_name")  # trigram word similarity
        params.append(q)
    base = f"""
        SELECT id, fda_product_code, brand_name, generic_name,
               manufacturer, device_class, medical_specialty, premarket_number
        FROM devices
        WHERE {" AND ".join(where) or "TRUE"}
    """
    if q:
        return await paginate(
            conn, base, params, order_by="word_similarity(%s, brand_name) DESC, brand_name", order_params=[q], page=page
        )
    return await paginate(conn, base, params, order_by="brand_name", page=page)


async def devices_by_procedure(conn, cpt: str) -> list[dict]:
    sql = """
        SELECT d.id, d.fda_product_code, d.brand_name, d.generic_name,
               d.manufacturer, d.device_class, d.medical_specialty,
               dpm.usage_type
        FROM device_procedure_map dpm
        JOIN devices d ON dpm.device_id = d.id
        WHERE dpm.cpt = %s
        ORDER BY d.brand_name
    """
    return await fetch_all(conn, sql, (cpt,))


async def get_device(conn, device_id: UUID) -> dict | None:
    sql = """
        SELECT id, fda_product_code, brand_name, generic_name,
               manufacturer, device_class, medical_specialty,
               premarket_number, description
        FROM devices WHERE id = %s
    """
    return await fetch_one(conn, sql, (device_id,))


async def recent_recalls(conn, device_id: UUID, limit: int = 10) -> list[dict]:
    sql = """
        SELECT recall_number, recall_class, reason, status,
               recall_date, termination_date
        FROM device_recalls
        WHERE device_id = %s
        ORDER BY recall_date DESC NULLS LAST
        LIMIT %s
    """
    return await fetch_all(conn, sql, (device_id, limit))


async def adverse_event_summary(conn, device_id: UUID) -> list[dict]:
    sql = """
        SELECT event_type, COUNT(*) AS count
        FROM device_adverse_events
        WHERE device_id = %s
        GROUP BY event_type
        ORDER BY count DESC
    """
    return await fetch_all(conn, sql, (device_id,))


async def list_recalls(conn: AsyncConnection, device_id: UUID, page: Pagination) -> tuple[list[dict], int]:
    base = """
        SELECT recall_number, product_code, brand_name, manufacturer,
               recall_class, reason, status, recall_date, termination_date,
               quantity, distribution
        FROM device_recalls
        WHERE device_id = %s
    """
    return await paginate(conn, base, [device_id], order_by="recall_date DESC NULLS LAST, recall_number", page=page)


async def list_adverse_events(
    conn: AsyncConnection, device_id: UUID, *, event_type: str | None, page: Pagination
) -> tuple[list[dict], int]:
    where = ["device_id = %s"]
    params: list = [device_id]
    if event_type:
        where.append("event_type = %s")
        params.append(event_type)
    base = f"""
        SELECT mdr_report_key, product_code, brand_name, manufacturer,
               event_type, event_date, patient_outcomes, device_problems,
               event_narrative
        FROM device_adverse_events
        WHERE {" AND ".join(where)}
    """
    return await paginate(conn, base, params, order_by="event_date DESC NULLS LAST, mdr_report_key", page=page)
