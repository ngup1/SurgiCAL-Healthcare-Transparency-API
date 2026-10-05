"""
Set up a hosted Postgres (e.g. Neon) for the API: apply migrations, then load mock data.

    DATABASE_URL=postgresql://... python api/db/seed_remote.py     (or: make seed-db)

Safe to re-run:
- Migrations are recorded in a schema_migrations table; each file is applied
  once, in its own transaction, so a failure leaves earlier ones in place.
- seed.sql truncates the data tables before inserting, so re-running resets the
  data to the generated mock data.

Use a *direct* (non-pooled) connection string here: schema changes are best run
outside a transaction-mode connection pooler.
"""

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

import psycopg2

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "migrations"
SEED_FILE = REPO_ROOT / "api" / "seed" / "seed.sql"

COUNT_TABLES = ["hospitals", "providers", "prices", "devices", "ca_places"]


def describe(url: str) -> str:
    """host/dbname for log output, without credentials."""
    parts = urlsplit(url)
    return f"{parts.hostname}{parts.path}"


def apply_migrations(conn) -> None:
    with conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              version    TEXT PRIMARY KEY,
              applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute("SELECT version FROM schema_migrations")
        applied = {row[0] for row in cur.fetchall()}

    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name in applied:
            print(f"  skip   {path.name}")
            continue
        # `with conn` commits on success and rolls back on error.
        with conn, conn.cursor() as cur:
            cur.execute(path.read_text())
            cur.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.name,))
        print(f"  apply  {path.name}")


def load_seed(conn) -> None:
    # seed.sql has its own BEGIN/COMMIT, so run it outside a driver-managed transaction.
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(SEED_FILE.read_text())
    conn.autocommit = False


def main() -> int:
    url = os.getenv("DATABASE_URL")
    if not url:
        print("Set DATABASE_URL to the database's connection string.", file=sys.stderr)
        return 2

    print(f"Connecting to {describe(url)}")
    conn = psycopg2.connect(url, connect_timeout=15)
    try:
        print("Migrations:")
        apply_migrations(conn)
        print(f"Loading {SEED_FILE.relative_to(REPO_ROOT)} ...")
        load_seed(conn)
        with conn.cursor() as cur:
            counts = []
            for table in COUNT_TABLES:
                cur.execute(f"SELECT count(*) FROM {table}")  # table names are constants above
                counts.append(f"{table}={cur.fetchone()[0]}")
        print("Done: " + ", ".join(counts))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
