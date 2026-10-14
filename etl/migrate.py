"""
Apply the SQL migrations in migrations/ to the ETL's database.

    python -m etl.migrate          (or: make etl-migrate)

Uses the same schema_migrations table as api/db/seed_remote.py, so either tool
can bring a database up to date. Unlike seed_remote it loads no mock data, which
makes it the one to run against a database that will hold real data.
"""
from __future__ import annotations

import sys
from pathlib import Path

from etl.common.config import get_logger
from etl.common.db import get_db_connection

logger = get_logger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def run() -> int:
    """Apply each migration not yet recorded, one transaction per file. Returns how many ran."""
    conn = get_db_connection()
    applied_now = 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                  version    TEXT PRIMARY KEY,
                  applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            conn.commit()
            cur.execute("SELECT version FROM schema_migrations")
            applied = {row[0] for row in cur.fetchall()}

            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if path.name in applied:
                    continue
                try:
                    cur.execute(path.read_text())
                    cur.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.name,))
                    conn.commit()
                except Exception:
                    conn.rollback()
                    logger.error(f"Migration {path.name} failed; earlier ones stay applied")
                    raise
                logger.info(f"Applied {path.name}")
                applied_now += 1
    finally:
        conn.close()

    logger.info(f"Migrations up to date ({applied_now} applied)")
    return applied_now


if __name__ == "__main__":
    run()
    sys.exit(0)
