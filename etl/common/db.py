"""Database utilities for PostgreSQL operations via psycopg2."""

from __future__ import annotations

from typing import Any

import psycopg2
from psycopg2.extras import execute_values, RealDictCursor

from etl.common.config import get_settings, get_logger

logger = get_logger(__name__)


def get_db_connection():
    """Get a PostgreSQL connection (direct RDS connect)."""
    settings = get_settings()
    return psycopg2.connect(**settings.db_connect_kwargs)


def upsert_batch(
    table: str,
    records: list[dict[str, Any]],
    conflict_columns: list[str],
    update_columns: list[str] | None = None,
    batch_size: int = 1000,
) -> int:
    """
    Upsert records into a table using INSERT ... ON CONFLICT DO UPDATE.

    Returns total number of records upserted.
    """
    if not records:
        return 0

    columns = list(records[0].keys())
    if update_columns is None:
        update_columns = [c for c in columns if c not in conflict_columns]

    conflict_clause = ", ".join(conflict_columns)
    if update_columns:
        set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_columns)
        on_conflict = f"ON CONFLICT ({conflict_clause}) DO UPDATE SET {set_clause}"
    else:
        on_conflict = f"ON CONFLICT ({conflict_clause}) DO NOTHING"

    insert_sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES %s {on_conflict}"

    conn = get_db_connection()
    total = 0
    try:
        with conn.cursor() as cur:
            for i in range(0, len(records), batch_size):
                batch = records[i : i + batch_size]
                values = [tuple(r[c] for c in columns) for r in batch]
                execute_values(cur, insert_sql, values)
                total += len(batch)
                logger.debug(f"Upserted batch {i // batch_size + 1} ({len(batch)} records)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    logger.info(f"Upserted {total} records to {table}")
    return total


def execute_query(
    query: str, params: tuple | dict | None = None
) -> list[dict]:
    """Execute a read query and return results as dicts."""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()
