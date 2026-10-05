"""Device, recall, and adverse-event queries."""

from uuid import UUID

from src.database import fetch_all, fetch_one


def list_devices(
    conn,
    *,
    product_code: str | None,
    manufacturer: str | None,
    medical_specialty: str | None,
    q: str | None,
    limit: int,
    offset: int,
) -> list[dict]:
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

    order = "brand_name ASC"
    if q:
        order = "word_similarity(%s, brand_name) DESC, brand_name ASC"
        params.append(q)

    sql = f"""
        SELECT id, fda_product_code, brand_name, generic_name,
               manufacturer, device_class, medical_specialty, premarket_number
        FROM devices
        WHERE {" AND ".join(where_parts) or "TRUE"}
        ORDER BY {order}
        LIMIT %s OFFSET %s
    """
    return fetch_all(conn, sql, params + [limit, offset])


def devices_by_procedure(conn, cpt: str) -> list[dict]:
    sql = """
        SELECT d.id, d.fda_product_code, d.brand_name, d.generic_name,
               d.manufacturer, d.device_class, d.medical_specialty,
               dpm.usage_type
        FROM device_procedure_map dpm
        JOIN devices d ON dpm.device_id = d.id
        WHERE dpm.cpt = %s
        ORDER BY d.brand_name
    """
    return fetch_all(conn, sql, (cpt,))


def get_device(conn, device_id: UUID) -> dict | None:
    sql = """
        SELECT id, fda_product_code, brand_name, generic_name,
               manufacturer, device_class, medical_specialty,
               premarket_number, description
        FROM devices WHERE id = %s
    """
    return fetch_one(conn, sql, (str(device_id),))


def recent_recalls(conn, device_id: UUID, limit: int = 10) -> list[dict]:
    sql = """
        SELECT recall_number, recall_class, reason, status,
               recall_date, termination_date
        FROM device_recalls
        WHERE device_id = %s
        ORDER BY recall_date DESC NULLS LAST
        LIMIT %s
    """
    return fetch_all(conn, sql, (str(device_id), limit))


def adverse_event_summary(conn, device_id: UUID) -> list[dict]:
    sql = """
        SELECT event_type, COUNT(*) AS count
        FROM device_adverse_events
        WHERE device_id = %s
        GROUP BY event_type
        ORDER BY count DESC
    """
    return fetch_all(conn, sql, (str(device_id),))


def list_recalls(conn, device_id: UUID, *, limit: int, offset: int) -> list[dict]:
    sql = """
        SELECT recall_number, product_code, brand_name, manufacturer,
               recall_class, reason, status, recall_date, termination_date,
               quantity, distribution
        FROM device_recalls
        WHERE device_id = %s
        ORDER BY recall_date DESC NULLS LAST
        LIMIT %s OFFSET %s
    """
    return fetch_all(conn, sql, (str(device_id), limit, offset))


def list_adverse_events(conn, device_id: UUID, *, event_type: str | None, limit: int, offset: int) -> list[dict]:
    where_parts = ["device_id = %s"]
    params: list = [str(device_id)]
    if event_type:
        where_parts.append("event_type = %s")
        params.append(event_type)

    sql = f"""
        SELECT mdr_report_key, product_code, brand_name, manufacturer,
               event_type, event_date, patient_outcomes, device_problems,
               event_narrative
        FROM device_adverse_events
        WHERE {" AND ".join(where_parts)}
        ORDER BY event_date DESC NULLS LAST
        LIMIT %s OFFSET %s
    """
    return fetch_all(conn, sql, params + [limit, offset])
