"""Database utilities for PostgreSQL operations via psycopg2."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import psycopg2
from psycopg2.extras import RealDictCursor, execute_values

from etl.common.config import get_logger, get_settings

logger = get_logger(__name__)

_warned_pooler = False


def get_db_connection():
    """Get a PostgreSQL connection from DATABASE_URL, or from DB_* when it isn't set."""
    global _warned_pooler
    settings = get_settings()
    if settings.database_url and not _warned_pooler:
        host = urlsplit(settings.database_url).hostname or ""
        if "-pooler" in host:
            # Neon's pooled endpoint runs PgBouncer in transaction mode, which cuts off
            # long transactions and session state. Loads should use the direct endpoint.
            logger.warning("DATABASE_URL points at a pooled endpoint (%s); use the direct one for ETL", host)
        _warned_pooler = True
    return psycopg2.connect(**settings.db_connect_kwargs)


def _columns_of(records: list[dict[str, Any]]) -> list[str]:
    """Every key used by any record, in first-seen order."""
    columns: dict[str, None] = {}
    for record in records:
        for key in record:
            columns.setdefault(key)
    return list(columns)


def _dedupe(records: list[dict[str, Any]], key_columns: list[str]) -> list[dict[str, Any]]:
    """Keep the last record for each key. Postgres rejects a batch that hits one row twice."""
    by_key: dict[tuple, dict[str, Any]] = {}
    for record in records:
        by_key[tuple(record.get(c) for c in key_columns)] = record
    if len(by_key) < len(records):
        logger.warning("Dropped %d records with a duplicate key", len(records) - len(by_key))
    return list(by_key.values())


def upsert_batch(
    table: str,
    records: list[dict[str, Any]],
    conflict_columns: list[str],
    update_columns: list[str] | None = None,
    batch_size: int = 1000,
) -> int:
    """
    Upsert records into a table using INSERT ... ON CONFLICT DO UPDATE.

    Records may have different keys: the column list is the union, and a record
    missing a column inserts NULL for it. Duplicate conflict keys are collapsed
    to the last record. Each batch commits on its own, so a failure late in a
    long load keeps the batches already written.

    Returns total number of records upserted.
    """
    if not records:
        return 0

    columns = _columns_of(records)
    records = _dedupe(records, conflict_columns)
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
                values = [tuple(r.get(c) for c in columns) for r in batch]
                execute_values(cur, insert_sql, values, page_size=batch_size)
                conn.commit()
                total += len(batch)
                logger.debug(f"Upserted batch {i // batch_size + 1} ({len(batch)} records)")
    except Exception:
        conn.rollback()
        logger.error("Upsert into %s failed after %d of %d records were committed", table, total, len(records))
        raise
    finally:
        conn.close()

    logger.info(f"Upserted {total} records to {table}")
    return total


def update_batch(
    table: str,
    records: list[dict[str, Any]],
    key_columns: list[str],
    update_columns: list[str] | None = None,
    batch_size: int = 1000,
) -> int:
    """
    Update existing rows only (UPDATE ... FROM (VALUES ...)); never inserts.

    For enrichment: records carry the key and a few columns, and rows whose key
    isn't in the table are ignored instead of failing NOT NULL checks the way an
    upsert would. A NULL or missing value never overwrites one already stored.

    Returns the number of rows updated.
    """
    if not records:
        return 0

    columns = _columns_of(records)
    records = _dedupe(records, key_columns)
    if update_columns is None:
        update_columns = [c for c in columns if c not in key_columns]
    if not update_columns:
        return 0

    conn = get_db_connection()
    updated = 0
    try:
        with conn.cursor() as cur:
            # VALUES lists have no column types of their own, so cast to the table's.
            cur.execute(
                """
                SELECT attname, format_type(atttypid, atttypmod)
                FROM pg_attribute
                WHERE attrelid = %s::regclass AND attnum > 0 AND NOT attisdropped
                """,
                (table,),
            )
            types = dict(cur.fetchall())
            unknown = [c for c in columns if c not in types]
            if unknown:
                raise ValueError(f"{table} has no column(s): {', '.join(unknown)}")

            # COALESCE: a record that lacks a value leaves the row's existing one alone.
            set_clause = ", ".join(f"{c} = COALESCE(v.{c}::{types[c]}, t.{c})" for c in update_columns)
            where_clause = " AND ".join(f"t.{c} = v.{c}::{types[c]}" for c in key_columns)
            update_sql = (
                f"UPDATE {table} AS t SET {set_clause} "
                f"FROM (VALUES %s) AS v ({', '.join(columns)}) WHERE {where_clause}"
            )
            for i in range(0, len(records), batch_size):
                batch = records[i : i + batch_size]
                values = [tuple(r.get(c) for c in columns) for r in batch]
                execute_values(cur, update_sql, values, page_size=batch_size)
                updated += cur.rowcount
                conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    logger.info(f"Updated {updated} of {len(records)} records in {table}")
    return updated


def replace_rows(
    table: str,
    records: list[dict[str, Any]],
    where: str,
    params: tuple,
    batch_size: int = 1000,
) -> int:
    """
    Delete the rows matching `where` and insert `records`, in one transaction.

    For data that is refreshed as a whole per owner (one hospital's prices):
    rows the source dropped disappear, and a failure leaves the old rows in place.
    """
    columns = _columns_of(records)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM {table} WHERE {where}", params)
            deleted = cur.rowcount
            if records:
                insert_sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES %s"
                values = [tuple(r.get(c) for c in columns) for r in records]
                execute_values(cur, insert_sql, values, page_size=batch_size)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    logger.info(f"Replaced {deleted} rows in {table} with {len(records)}")
    return len(records)


def execute_query(query: str, params: tuple | dict | None = None) -> list[dict]:
    """Execute a read query and return results as dicts."""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def execute(statement: str, params: tuple | dict | None = None) -> int:
    """Execute a write statement in its own transaction and return the affected row count."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(statement, params)
            count = cur.rowcount
        conn.commit()
        return count
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
