"""Hospital endpoints: list, detail, affiliated providers."""

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from psycopg2.extras import RealDictCursor

from api.dependencies import distance_select, get_db, spatial_where
from api.validation import CCN_PATTERN, STATE_PATTERN, require_lat_lng_pair

router = APIRouter()


@router.get("")
def list_hospitals(
    state: str = Query("CA", pattern=STATE_PATTERN, description="Two-letter state code"),
    lat: float | None = Query(None, ge=-90, le=90),
    lng: float | None = Query(None, ge=-180, le=180),
    radius_miles: float = Query(25, gt=0, le=250),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn=Depends(get_db),
):
    """List hospitals, optionally filtered by location."""
    require_lat_lng_pair(lat, lng)
    dist_sql, dist_params = distance_select(lat, lng)
    geo_clause, geo_params = spatial_where(lat, lng, radius_miles)

    where_parts = ["state = %s"]
    # dist_params first: SELECT clause %s placeholders come before WHERE clause
    params: list = dist_params + [state]

    if geo_clause:
        where_parts.append(geo_clause)
        params.extend(geo_params)

    where_sql = " AND ".join(where_parts)
    order = "distance_miles ASC NULLS LAST" if lat else "name ASC"

    sql = f"""
        SELECT ccn, name, address, city, state, zip, phone,
               hospital_type, ownership, emergency_services,
               ST_Y(location::geometry) AS lat, ST_X(location::geometry) AS lng,
               {dist_sql}
        FROM hospitals
        WHERE {where_sql}
        ORDER BY {order}
        LIMIT %s OFFSET %s
    """
    params.extend([limit, offset])

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


@router.get("/{ccn}")
def get_hospital(ccn: str = Path(..., pattern=CCN_PATTERN), conn=Depends(get_db)):
    """Get hospital detail including quality metrics."""
    sql = """
        SELECT h.ccn, h.name, h.address, h.city, h.state, h.zip, h.phone,
               h.hospital_type, h.ownership, h.emergency_services,
               ST_Y(h.location::geometry) AS lat, ST_X(h.location::geometry) AS lng,
               hq.overall_stars, hq.mortality_group, hq.safety_group,
               hq.readmission_group, hq.patient_experience_group,
               hq.timely_care_group, hq.psi90_composite, hq.hai_sirs,
               hq.readmission_hip_knee, hq.complication_hip_knee,
               hq.mortality_cabg, hq.measure_period
        FROM hospitals h
        LEFT JOIN hospital_quality hq ON h.ccn = hq.ccn
        WHERE h.ccn = %s
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, (ccn,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Hospital not found")
        return dict(row)


@router.get("/{ccn}/providers")
def list_hospital_providers(
    ccn: str = Path(..., pattern=CCN_PATTERN),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn=Depends(get_db),
):
    """List providers affiliated with a hospital."""
    sql = """
        SELECT p.npi, p.first_name, p.last_name, p.credential, p.specialty,
               pm.patient_rating, pm.num_reviews, pm.volume_bucket, pm.wrvu_estimate,
               pa.is_primary
        FROM provider_affiliations pa
        JOIN providers p ON pa.npi = p.npi
        LEFT JOIN provider_metrics pm ON p.npi = pm.npi
        WHERE pa.ccn = %s
        ORDER BY pm.wrvu_estimate DESC NULLS LAST
        LIMIT %s OFFSET %s
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, (ccn, limit, offset))
        return cur.fetchall()
