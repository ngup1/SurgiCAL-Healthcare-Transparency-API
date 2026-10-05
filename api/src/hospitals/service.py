"""Hospital queries."""

from psycopg import AsyncConnection

from src.database import fetch_one
from src.locations.schemas import LocationFilter
from src.locations.service import location_sql
from src.pagination import Pagination, paginate


async def list_hospitals(conn: AsyncConnection, location: LocationFilter, page: Pagination) -> tuple[list[dict], int]:
    dist_sql, dist_params, where, where_params = location_sql(location, city_col="h.city", location_col="h.location")
    base = f"""
        SELECT h.ccn, h.name, h.address, h.city, h.state, h.zip, h.phone,
               h.hospital_type, h.ownership, h.emergency_services,
               ST_Y(h.location::geometry) AS lat, ST_X(h.location::geometry) AS lng,
               {dist_sql}
        FROM hospitals h
        WHERE {" AND ".join(where) or "TRUE"}
    """
    order_by = "distance_miles ASC" if location.is_radius else "name ASC"
    return await paginate(conn, base, dist_params + where_params, order_by=order_by, page=page)


async def get_hospital(conn: AsyncConnection, ccn: str) -> dict | None:
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
    return await fetch_one(conn, sql, (ccn,))


async def list_hospital_providers(conn: AsyncConnection, ccn: str, page: Pagination) -> tuple[list[dict], int]:
    base = """
        SELECT p.npi, p.first_name, p.last_name, p.credential, p.specialty,
               pm.patient_rating, pm.num_reviews, pm.volume_bucket, pm.wrvu_estimate,
               pa.is_primary
        FROM provider_affiliations pa
        JOIN providers p ON pa.npi = p.npi
        LEFT JOIN provider_metrics pm ON p.npi = pm.npi
        WHERE pa.ccn = %s
    """
    return await paginate(conn, base, [ccn], order_by="wrvu_estimate DESC NULLS LAST, npi", page=page)
