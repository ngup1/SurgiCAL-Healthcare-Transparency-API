"""Price queries."""

from src.database import fetch_all
from src.geo import distance_select, spatial_where


async def search_prices(
    conn,
    *,
    cpt: str,
    lat: float | None,
    lng: float | None,
    radius_miles: float,
    payer: str | None,
    limit: int,
    offset: int,
) -> list[dict]:
    dist_sql, dist_params = distance_select(lat, lng, column="h.location")
    geo_clause, geo_params = spatial_where(lat, lng, radius_miles, column="h.location")

    where_parts = ["pr.cpt = %s"]
    # dist_params first: SELECT clause %s placeholders come before WHERE clause
    params: list = dist_params + [cpt]
    if payer:
        where_parts.append("pr.payer ILIKE %s")
        params.append(f"%{payer}%")
    if geo_clause:
        where_parts.append(geo_clause)
        params.extend(geo_params)

    sql = f"""
        SELECT pr.cpt, c.description AS procedure_name,
               h.ccn, h.name AS hospital_name, h.city, h.state,
               pr.payer, pr.plan_name, pr.billing_class,
               pr.cash_price, pr.negotiated_rate, pr.negotiated_min, pr.negotiated_max,
               pr.source, pr.measure_date,
               hq.overall_stars, hq.psi90_composite,
               {dist_sql}
        FROM prices pr
        JOIN hospitals h ON pr.ccn = h.ccn
        JOIN cpt_codes c ON pr.cpt = c.code
        LEFT JOIN hospital_quality hq ON h.ccn = hq.ccn
        WHERE {" AND ".join(where_parts)}
        ORDER BY pr.negotiated_rate ASC NULLS LAST
        LIMIT %s OFFSET %s
    """
    return await fetch_all(conn, sql, params + [limit, offset])


async def compare_prices(conn, *, cpt: str, ccns: list[str]) -> list[dict]:
    sql = """
        SELECT pr.cpt, c.description AS procedure_name,
               h.ccn, h.name AS hospital_name, h.city,
               pr.payer, pr.plan_name, pr.billing_class,
               pr.cash_price, pr.negotiated_rate, pr.negotiated_min, pr.negotiated_max,
               hq.overall_stars, hq.psi90_composite
        FROM prices pr
        JOIN hospitals h ON pr.ccn = h.ccn
        JOIN cpt_codes c ON pr.cpt = c.code
        LEFT JOIN hospital_quality hq ON h.ccn = hq.ccn
        WHERE pr.cpt = %s AND pr.ccn = ANY(%s)
        ORDER BY h.name, pr.payer
    """
    return await fetch_all(conn, sql, (cpt, ccns))
