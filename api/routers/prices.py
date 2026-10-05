"""Pricing endpoints: search by CPT/location/payer, compare hospitals."""

import re

from fastapi import APIRouter, Depends, Query
from psycopg2.extras import RealDictCursor

from api.dependencies import distance_select, get_db, spatial_where
from api.docs import CCNS_EXAMPLES, CPT_EXAMPLES
from api.validation import (
    CCN_PATTERN,
    CPT_PATTERN,
    MAX_COMPARE_HOSPITALS,
    raise_validation_error,
    require_lat_lng_pair,
)

router = APIRouter()


@router.get("", summary="Prices for a procedure")
def search_prices(
    cpt: str = Query(..., pattern=CPT_PATTERN, description="CPT procedure code", openapi_examples=CPT_EXAMPLES),
    lat: float | None = Query(None, ge=-90, le=90),
    lng: float | None = Query(None, ge=-180, le=180),
    radius_miles: float = Query(50, gt=0, le=250),
    payer: str | None = Query(None, description="Partial match on insurer, e.g. `aetna`, `medicare`, `cash`"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn=Depends(get_db),
):
    """
    Every price for a procedure, cheapest negotiated rate first: one row per hospital, insurer,
    and plan, with the hospital's cash price, rate range, and quality rating.

    Pass `lat` and `lng` together to limit to hospitals within `radius_miles`.
    """
    require_lat_lng_pair(lat, lng)
    dist_sql, dist_params = distance_select(lat, lng)
    # Replace generic 'location' with table-qualified column
    dist_sql = dist_sql.replace("location", "h.location")
    geo_clause, geo_params = spatial_where(lat, lng, radius_miles)

    where_parts = ["pr.cpt = %s"]
    # dist_params first: SELECT clause %s placeholders come before WHERE clause
    params: list = dist_params + [cpt]

    if payer:
        where_parts.append("pr.payer ILIKE %s")
        params.append(f"%{payer}%")

    if geo_clause:
        where_parts.append(geo_clause.replace("location", "h.location"))
        params.extend(geo_params)

    where_sql = " AND ".join(where_parts)

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
        WHERE {where_sql}
        ORDER BY pr.negotiated_rate ASC NULLS LAST
        LIMIT %s OFFSET %s
    """
    params.extend([limit, offset])

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


@router.get("/compare", summary="Compare a procedure across hospitals")
def compare_prices(
    cpt: str = Query(..., pattern=CPT_PATTERN, description="CPT procedure code", openapi_examples=CPT_EXAMPLES),
    ccns: str = Query(..., description="Comma-separated hospital CCNs (up to 10)", openapi_examples=CCNS_EXAMPLES),
    conn=Depends(get_db),
):
    """Side-by-side prices for one procedure at the hospitals you choose, grouped by hospital and insurer."""
    ccn_list = [c.strip() for c in ccns.split(",") if c.strip()]
    if not ccn_list:
        raise_validation_error("ccns", "At least one hospital CCN is required", ccns)
    if len(ccn_list) > MAX_COMPARE_HOSPITALS:
        raise_validation_error("ccns", f"At most {MAX_COMPARE_HOSPITALS} hospitals can be compared", ccns)
    bad = [c for c in ccn_list if not re.fullmatch(CCN_PATTERN, c)]
    if bad:
        raise_validation_error("ccns", f"Not a valid 6-character CCN: {', '.join(bad)}", ccns)

    placeholders = ", ".join(["%s"] * len(ccn_list))
    sql = f"""
        SELECT pr.cpt, c.description AS procedure_name,
               h.ccn, h.name AS hospital_name, h.city,
               pr.payer, pr.plan_name, pr.billing_class,
               pr.cash_price, pr.negotiated_rate, pr.negotiated_min, pr.negotiated_max,
               hq.overall_stars, hq.psi90_composite
        FROM prices pr
        JOIN hospitals h ON pr.ccn = h.ccn
        JOIN cpt_codes c ON pr.cpt = c.code
        LEFT JOIN hospital_quality hq ON h.ccn = hq.ccn
        WHERE pr.cpt = %s AND pr.ccn IN ({placeholders})
        ORDER BY h.name, pr.payer
    """
    params = [cpt] + ccn_list

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        return cur.fetchall()
