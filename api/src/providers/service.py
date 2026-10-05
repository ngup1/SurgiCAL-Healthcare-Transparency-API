"""Provider queries."""

from psycopg import AsyncConnection

from src.database import fetch_all, fetch_one
from src.locations.schemas import LocationFilter
from src.locations.service import location_sql
from src.pagination import Pagination, paginate


async def list_providers(
    conn: AsyncConnection, *, specialty: str | None, location: LocationFilter, page: Pagination
) -> tuple[list[dict], int]:
    dist_sql, dist_params, where, where_params = location_sql(location, city_col="p.city", location_col="p.location")
    if specialty:
        where.append("p.specialty ILIKE %s")
        where_params.append(f"%{specialty}%")
    base = f"""
        SELECT p.npi, p.first_name, p.last_name, p.credential, p.specialty,
               p.city, p.state,
               pm.patient_rating, pm.num_reviews, pm.volume_bucket, pm.wrvu_estimate,
               {dist_sql}
        FROM providers p
        LEFT JOIN provider_metrics pm ON p.npi = pm.npi
        WHERE {" AND ".join(where) or "TRUE"}
    """
    order_by = "distance_miles ASC" if location.is_radius else "last_name ASC, first_name ASC"
    return await paginate(conn, base, dist_params + where_params, order_by=order_by, page=page)


async def get_provider(conn: AsyncConnection, npi: str) -> dict | None:
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


async def list_affiliations(conn: AsyncConnection, npi: str) -> list[dict]:
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
