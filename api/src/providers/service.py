"""Provider queries."""

from src.database import fetch_all, fetch_one
from src.geo import distance_select, spatial_where


async def list_providers(
    conn,
    *,
    specialty: str | None,
    state: str,
    city: str | None,
    lat: float | None,
    lng: float | None,
    radius_miles: float,
    limit: int,
    offset: int,
) -> list[dict]:
    dist_sql, dist_params = distance_select(lat, lng, column="p.location")
    geo_clause, geo_params = spatial_where(lat, lng, radius_miles, column="p.location")

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
        where_parts.append(geo_clause)
        params.extend(geo_params)

    order = "distance_miles ASC NULLS LAST" if lat is not None else "p.last_name ASC"
    sql = f"""
        SELECT p.npi, p.first_name, p.last_name, p.credential, p.specialty,
               p.city, p.state,
               pm.patient_rating, pm.num_reviews, pm.volume_bucket, pm.wrvu_estimate,
               {dist_sql}
        FROM providers p
        LEFT JOIN provider_metrics pm ON p.npi = pm.npi
        WHERE {" AND ".join(where_parts)}
        ORDER BY {order}
        LIMIT %s OFFSET %s
    """
    return await fetch_all(conn, sql, params + [limit, offset])


async def get_provider(conn, npi: str) -> dict | None:
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
    return await fetch_one(conn, sql, (npi,))


async def list_affiliations(conn, npi: str) -> list[dict]:
    sql = """
        SELECT h.ccn, h.name, h.city, h.state,
               hq.overall_stars, hq.psi90_composite,
               pa.is_primary
        FROM provider_affiliations pa
        JOIN hospitals h ON pa.ccn = h.ccn
        LEFT JOIN hospital_quality hq ON h.ccn = hq.ccn
        WHERE pa.npi = %s
        ORDER BY pa.is_primary DESC, h.name ASC
    """
    return await fetch_all(conn, sql, (npi,))
