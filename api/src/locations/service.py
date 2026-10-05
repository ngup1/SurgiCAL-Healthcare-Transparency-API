"""California place lookup (ca_places) and the SQL each location filter adds."""

from psycopg import AsyncConnection

from src.database import fetch_all, fetch_one
from src.geo import distance_select, spatial_where
from src.locations.schemas import LocationFilter


async def find_place(conn: AsyncConnection, place_type: str, name: str) -> dict | None:
    """Case-insensitive exact match; returns the canonical name and centroid."""
    sql = """
        SELECT name, county, ST_Y(location::geometry) AS lat, ST_X(location::geometry) AS lng
        FROM ca_places
        WHERE place_type = %s AND lower(name) = lower(%s)
    """
    return await fetch_one(conn, sql, (place_type, name))


async def suggest_places(conn: AsyncConnection, place_type: str, name: str, limit: int = 3) -> list[str]:
    """Close spellings for "did you mean" (trigram similarity)."""
    sql = """
        SELECT name FROM ca_places
        WHERE place_type = %s AND similarity(name, %s) > 0.3
        ORDER BY similarity(name, %s) DESC, name
        LIMIT %s
    """
    return [r["name"] for r in await fetch_all(conn, sql, (place_type, name, name, limit))]


async def search_places(conn: AsyncConnection, q: str, place_type: str | None, limit: int) -> list[dict]:
    """Autocomplete: names starting with q first, then fuzzy matches."""
    sql = """
        SELECT name, place_type AS type, county
        FROM ca_places
        WHERE (%s::text IS NULL OR place_type = %s)
          AND (lower(name) LIKE lower(%s) || '%%' OR %s <%% name)
        ORDER BY (lower(name) LIKE lower(%s) || '%%') DESC, word_similarity(%s, name) DESC, name
        LIMIT %s
    """
    return await fetch_all(conn, sql, (place_type, place_type, q, q, q, q, limit))


def location_sql(f: LocationFilter, *, city_col: str, location_col: str) -> tuple[str, list, list[str], list]:
    """
    SQL for a resolved location: (distance select expression, its params,
    WHERE fragments, their params). distance_miles is NULL unless it's a radius search.
    """
    dist_sql, dist_params = distance_select(f.lat, f.lng, column=location_col)
    where: list[str] = []
    params: list = []
    if f.is_radius:
        clause, clause_params = spatial_where(f.lat, f.lng, f.radius_miles, column=location_col)
        where.append(clause)
        params.extend(clause_params)
    elif f.city:
        where.append(f"{city_col} = %s")
        params.append(f.city)
    elif f.county:
        where.append(f"{city_col} IN (SELECT name FROM ca_places WHERE place_type = 'city' AND county = %s)")
        params.append(f.county)
    return dist_sql, dist_params, where, params
