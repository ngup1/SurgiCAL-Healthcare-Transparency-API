"""FastAPI dependencies: database sessions, common query helpers."""

import os
from collections.abc import Generator

import psycopg2
from dotenv import load_dotenv

load_dotenv()


def get_db_connection():
    """Get a PostgreSQL connection (direct RDS connect)."""
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5432")),
        dbname=os.getenv("DB_NAME", "surgical"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", ""),
        sslmode=os.getenv("DB_SSL_MODE", "require"),
    )


def get_db() -> Generator:
    """Yield a database connection for request lifecycle."""
    conn = get_db_connection()
    try:
        yield conn
    finally:
        conn.close()


def spatial_where(lat: float | None, lng: float | None, radius_miles: float = 25) -> tuple[str, list]:
    """
    Build a PostGIS WHERE clause fragment for distance filtering.

    Returns (sql_fragment, params) to be appended to a WHERE clause.
    The sql_fragment references the `location` column.
    """
    if lat is None or lng is None:
        return "", []

    radius_meters = radius_miles * 1609.34
    return (
        "ST_DWithin(location, ST_MakePoint(%s, %s)::geography, %s)",
        [lng, lat, radius_meters],
    )


def distance_select(lat: float | None, lng: float | None) -> tuple[str, list]:
    """
    Build a PostGIS SELECT expression for distance in miles.

    Returns (sql_expression, params) where sql_expression uses %s placeholders.
    Caller must insert the sql string into the SELECT clause and extend params.
    """
    if lat is None or lng is None:
        return "NULL AS distance_miles", []
    return (
        "ROUND((ST_Distance(location, ST_MakePoint(%s, %s)::geography) / 1609.34)::numeric, 1) AS distance_miles",
        [lng, lat],
    )
