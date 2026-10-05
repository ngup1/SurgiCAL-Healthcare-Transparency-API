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

import psycopg

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "migrations"
SEED_FILE = REPO_ROOT / "api" / "seed" / "seed.sql"

COUNT_TABLES = ["hospitals", "providers", "prices", "devices", "ca_places"]


def describe(url: str) -> str:
    """host/dbname for log output, without credentials."""
    parts = urlsplit(url)
    return f"{parts.hostname}{parts.path}"


def apply_migrations(conn: psycopg.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version    TEXT PRIMARY KEY,
          applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}

    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name in applied:
            print(f"  skip   {path.name}")
            continue
        # Commits on success, rolls back on error. (Unlike psycopg2, `with conn:` would
        # close the connection in psycopg 3, so use an explicit transaction block.)
        with conn.transaction():
            conn.execute(path.read_text())  # no parameters, so multiple statements are allowed
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.name,))
        print(f"  apply  {path.name}")


def load_seed(conn: psycopg.Connection) -> None:
    # seed.sql has its own BEGIN/COMMIT; the connection is in autocommit mode.
    conn.execute(SEED_FILE.read_text())


def main() -> int:
    url = os.getenv("DATABASE_URL")
    if not url:
        print("Set DATABASE_URL to the database's connection string.", file=sys.stderr)
        return 2

    print(f"Connecting to {describe(url)}")
    conn = psycopg.connect(url, connect_timeout=15, autocommit=True)
    try:
        print("Migrations:")
        apply_migrations(conn)
        print(f"Loading {SEED_FILE.relative_to(REPO_ROOT)} ...")
        load_seed(conn)
        counts = []
        for table in COUNT_TABLES:
            (count,) = conn.execute(f"SELECT count(*) FROM {table}").fetchone()  # constant table names
            counts.append(f"{table}={count}")
        print("Done: " + ", ".join(counts))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
