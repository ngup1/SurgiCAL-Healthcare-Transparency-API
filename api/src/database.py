"""Database connections."""

from collections.abc import Generator

import psycopg2
from psycopg2.extras import RealDictCursor

from src.config import settings


def get_db_connection():
    return psycopg2.connect(**settings.db_connect_args)


def get_db() -> Generator:
    """Yield a database connection for the request, closed afterwards."""
    conn = get_db_connection()
    try:
        yield conn
    finally:
        conn.close()


def fetch_all(conn, sql: str, params=()) -> list[dict]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def fetch_one(conn, sql: str, params=()) -> dict | None:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None
