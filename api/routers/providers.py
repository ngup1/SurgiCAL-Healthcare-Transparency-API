"""Provider/surgeon endpoints: list, detail with metrics and affiliations."""

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from psycopg2.extras import RealDictCursor

from api.dependencies import distance_select, get_db, spatial_where
from api.docs import NPI_EXAMPLES, not_found
from api.validation import NPI_PATTERN, STATE_PATTERN, require_lat_lng_pair

router = APIRouter()


@router.get("", summary="List providers")
def list_providers(
    specialty: str | None = Query(None, description="Partial match, e.g. `ortho` or `cardio`"),
    state: str = Query("CA", pattern=STATE_PATTERN, description="Two-letter state code"),
    city: str | None = Query(None, description="Partial match, e.g. `san` or `los angeles`"),
    lat: float | None = Query(None, ge=-90, le=90),
    lng: float | None = Query(None, ge=-180, le=180),
    radius_miles: float = Query(25, gt=0, le=250),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn=Depends(get_db),
):
    """
    List providers, sorted by last name. Filter by specialty and city, or pass `lat` and
    `lng` together to search within `radius_miles` (nearest first).
    """
    require_lat_lng_pair(lat, lng)
    dist_sql, dist_params = distance_select(lat, lng)
    # Replace generic 'location' with table-qualified column
    dist_sql = dist_sql.replace("location", "p.location")
    geo_clause, geo_params = spatial_where(lat, lng, radius_miles)

    where_parts = ["p.state = %s"]
    # dist_params first: SELECT clause %s placeholders come before WHERE clause
    params: list = dist_params + [state]

    if specialty:
        where_parts.append("p.specialty ILIKE %s")
        params.append(f"%{specialty}%")

    if city:
        where_parts.append("p.city ILIKE %s")
        params.append(f"%{city}%")

    if geo_clause:
        where_parts.append(geo_clause.replace("location", "p.location"))
        params.extend(geo_params)

    where_sql = " AND ".join(where_parts)
    order = "distance_miles ASC NULLS LAST" if lat else "p.last_name ASC"

    sql = f"""
        SELECT p.npi, p.first_name, p.last_name, p.credential, p.specialty,
               p.city, p.state,
               pm.patient_rating, pm.num_reviews, pm.volume_bucket, pm.wrvu_estimate,
               {dist_sql}
        FROM providers p
        LEFT JOIN provider_metrics pm ON p.npi = pm.npi
        WHERE {where_sql}
        ORDER BY {order}
        LIMIT %s OFFSET %s
    """
    params.extend([limit, offset])

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


@router.get("/{npi}", summary="Get a provider", responses=not_found("No provider has this NPI"))
def get_provider(
    npi: str = Path(
        ..., pattern=NPI_PATTERN, description="National Provider Identifier", openapi_examples=NPI_EXAMPLES
    ),
    conn=Depends(get_db),
):
    """Provider details, volume and rating metrics, and hospital affiliations (primary first)."""
    sql = """
        SELECT p.npi, p.first_name, p.last_name, p.credential, p.specialty,
               p.taxonomy_code, p.gender, p.medical_school, p.graduation_year,
               p.city, p.state,
               pm.patient_rating, pm.num_reviews,
               pm.total_medicare_services, pm.total_medicare_beneficiaries,
               pm.wrvu_estimate, pm.volume_bucket,
               pm.trilliant_specialty, pm.trilliant_active,
               pm.patient_demographics, pm.has_sanctions
        FROM providers p
        LEFT JOIN provider_metrics pm ON p.npi = pm.npi
        WHERE p.npi = %s
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, (npi,))
        provider = cur.fetchone()
        if not provider:
            raise HTTPException(status_code=404, detail="Provider not found")

        cur.execute(
            """
            SELECT h.ccn, h.name, h.city, h.state,
                   hq.overall_stars, hq.psi90_composite,
                   pa.is_primary
            FROM provider_affiliations pa
            JOIN hospitals h ON pa.ccn = h.ccn
            LEFT JOIN hospital_quality hq ON h.ccn = hq.ccn
            WHERE pa.npi = %s
            ORDER BY pa.is_primary DESC, h.name ASC
            """,
            (npi,),
        )
        affiliations = cur.fetchall()

    result = dict(provider)
    result["affiliations"] = [dict(a) for a in affiliations]
    return result
