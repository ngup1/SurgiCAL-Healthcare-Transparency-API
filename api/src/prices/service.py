"""Price queries."""

from psycopg import AsyncConnection

from src.database import fetch_all
from src.locations.schemas import LocationFilter
from src.locations.service import location_sql
from src.pagination import Pagination, paginate

PRICE_COLUMNS = """
    pr.cpt, c.description AS procedure_name,
    h.ccn, h.name AS hospital_name, h.city,
    pr.payer, pr.plan_name, pr.billing_class,
    pr.cash_price, pr.negotiated_rate, pr.negotiated_min, pr.negotiated_max,
    hq.overall_stars, hq.psi90_composite
"""
PRICE_JOINS = """
    FROM prices pr
    JOIN hospitals h ON pr.ccn = h.ccn
    JOIN cpt_codes c ON pr.cpt = c.code
    LEFT JOIN hospital_quality hq ON h.ccn = hq.ccn
"""


async def search_prices(
    conn: AsyncConnection, *, cpt: str, payer: str | None, location: LocationFilter, page: Pagination
) -> tuple[list[dict], int]:
    dist_sql, dist_params, where, where_params = location_sql(location, city_col="h.city", location_col="h.location")
    where.insert(0, "pr.cpt = %s")
    where_params.insert(0, cpt)
    if payer:
        where.append("pr.payer ILIKE %s")
        where_params.append(f"%{payer}%")
    base = f"""
        SELECT {PRICE_COLUMNS}, h.state, pr.source, pr.measure_date, {dist_sql}
        {PRICE_JOINS}
        WHERE {" AND ".join(where)}
    """
    return await paginate(
        conn,
        base,
        dist_params + where_params,
        order_by="negotiated_rate ASC NULLS LAST, hospital_name, payer, plan_name",
        page=page,
    )


async def compare_prices(conn: AsyncConnection, *, cpt: str, ccns: list[str]) -> list[dict]:
    sql = f"""
        SELECT {PRICE_COLUMNS}
        {PRICE_JOINS}
        WHERE pr.cpt = %s AND pr.ccn = ANY(%s)
        ORDER BY h.name, pr.payer, pr.plan_name
    """
    return await fetch_all(conn, sql, (cpt, ccns))
