"""Async database access: one connection pool per worker process.

The pool is opened in the app's lifespan (src/main.py) and stored on app.state.
Requests borrow a connection for their duration instead of opening a new one.
"""

from collections.abc import AsyncGenerator

from fastapi import Request
from psycopg import AsyncConnection, sql
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from src.config import settings


def create_pool() -> AsyncConnectionPool:
    return AsyncConnectionPool(
        conninfo=settings.db_conninfo,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        # Fail fast with a 503 instead of holding requests for 30 s when the database is down.
        timeout=settings.db_pool_timeout,
        open=False,
        kwargs={
            "row_factory": dict_row,
            # Read-only queries; no transaction left open while a connection sits in the pool.
            "autocommit": True,
            # psycopg prepares frequently-run statements on the server. Transaction-mode
            # poolers (Neon's -pooler endpoint, PgBouncer) can route the next query to a
            # different server connection, where that prepared statement doesn't exist.
            "prepare_threshold": None,
        },
    )


def get_pool(request: Request) -> AsyncConnectionPool:
    return request.app.state.pool


async def get_db(request: Request) -> AsyncGenerator[AsyncConnection, None]:
    """Borrow a pooled connection for the request; it goes back to the pool afterwards."""
    async with request.app.state.pool.connection() as conn:
        yield conn


SQLQuery = str | sql.Composable


async def fetch_all(conn: AsyncConnection, query: SQLQuery, params=()) -> list[dict]:
    async with conn.cursor() as cur:
        await cur.execute(query, params)
        return await cur.fetchall()


async def fetch_one(conn: AsyncConnection, query: SQLQuery, params=()) -> dict | None:
    async with conn.cursor() as cur:
        await cur.execute(query, params)
        return await cur.fetchone()
