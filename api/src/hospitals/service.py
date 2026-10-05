"""Hospital queries."""

from src.database import fetch_all, fetch_one
from src.geo import distance_select, spatial_where


def list_hospitals(
    conn, *, state: str, lat: float | None, lng: float | None, radius_miles: float, limit: int, offset: int
) -> list[dict]:
    dist_sql, dist_params = distance_select(lat, lng)
    geo_clause, geo_params = spatial_where(lat, lng, radius_miles)

    where_parts = ["state = %s"]
    # dist_params first: SELECT clause %s placeholders come before WHERE clause
    params: list = dist_params + [state]
    if geo_clause:
        where_parts.append(geo_clause)
        params.extend(geo_params)

    order = "distance_miles ASC NULLS LAST" if lat is not None else "name ASC"
    sql = f"""
        SELECT ccn, name, address, city, state, zip, phone,
               hospital_type, ownership, emergency_services,
               ST_Y(location::geometry) AS lat, ST_X(location::geometry) AS lng,
               {dist_sql}
        FROM hospitals
        WHERE {" AND ".join(where_parts)}
        ORDER BY {order}
        LIMIT %s OFFSET %s
    """
    return fetch_all(conn, sql, params + [limit, offset])


def get_hospital(conn, ccn: str) -> dict | None:
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
    return fetch_one(conn, sql, (ccn,))


def list_hospital_providers(conn, ccn: str, *, limit: int, offset: int) -> list[dict]:
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
    return fetch_all(conn, sql, (ccn, limit, offset))
