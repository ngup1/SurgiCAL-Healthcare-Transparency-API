"""
Checks that decide whether a refreshed database may replace the one in service.

    python -m etl.checks --baseline-url "$PRODUCTION_DATABASE_URL"

Compares the database the ETL just loaded (DATABASE_URL) with a baseline:
- a table that lost more than --max-shrink of its rows fails (a source that
  silently returned less looks exactly like this);
- a core table that is empty fails;
- a database larger than --storage-limit-mb fails, and one past 80% of it warns.

Exit code 0 means the refresh can be promoted.
"""
from __future__ import annotations

import argparse
import sys

import psycopg2

from etl.common.config import get_logger
from etl.common.db import get_db_connection

logger = get_logger(__name__)

# Every table the pipeline loads.
TABLES = [
    "ca_places", "hospitals", "hospital_quality", "cpt_codes", "providers", "provider_metrics",
    "provider_procedure_volumes", "devices", "device_procedure_map", "device_recalls",
    "device_adverse_events", "hospital_mrf_links", "prices",
]
# The API is of no use without these.
REQUIRED = ["ca_places", "hospitals", "cpt_codes", "providers", "devices", "prices"]

DEFAULT_MAX_SHRINK = 0.20
DEFAULT_STORAGE_LIMIT_MB = 1024  # Neon's free tier


def table_counts(conn) -> dict[str, int]:
    """Row count per pipeline table; a table that doesn't exist yet counts as 0."""
    counts = {}
    with conn.cursor() as cur:
        for table in TABLES:
            cur.execute("SELECT to_regclass(%s) IS NOT NULL", (table,))
            if cur.fetchone()[0]:
                cur.execute(f"SELECT count(*) FROM {table}")  # names from TABLES above
                counts[table] = cur.fetchone()[0]
            else:
                counts[table] = 0
    return counts


def database_size_mb(conn) -> float:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_database_size(current_database())")
        return cur.fetchone()[0] / (1024 * 1024)


def compare_counts(new: dict[str, int], baseline: dict[str, int] | None, max_shrink: float) -> list[str]:
    """Problems found comparing the new row counts with the baseline's (none if the list is empty)."""
    problems = [f"{table} is empty" for table in REQUIRED if new.get(table, 0) == 0]
    for table, before in (baseline or {}).items():
        after = new.get(table, 0)
        if before > 0 and after < before * (1 - max_shrink):
            lost = 100 * (before - after) / before
            problems.append(f"{table} shrank by {lost:.0f}% ({before} -> {after} rows)")
    return problems


def check_storage(size_mb: float, limit_mb: float) -> tuple[str | None, str | None]:
    """(problem, warning) for the database size against the storage limit."""
    if size_mb > limit_mb:
        return f"database is {size_mb:.0f} MB, over the {limit_mb:.0f} MB limit", None
    if size_mb > 0.8 * limit_mb:
        return None, f"database is {size_mb:.0f} MB, {100 * size_mb / limit_mb:.0f}% of the {limit_mb:.0f} MB limit"
    return None, None


def main() -> int:
    parser = argparse.ArgumentParser(description="Check a refreshed database before promoting it")
    parser.add_argument("--baseline-url", help="Connection string of the database in service (omit on a first load)")
    parser.add_argument("--max-shrink", type=float, default=DEFAULT_MAX_SHRINK,
                        help="Largest fraction of rows a table may lose (default 0.20)")
    parser.add_argument("--storage-limit-mb", type=float, default=DEFAULT_STORAGE_LIMIT_MB,
                        help="Fail above this database size (default 1024, Neon's free tier)")
    args = parser.parse_args()

    conn = get_db_connection()
    try:
        new, size_mb = table_counts(conn), database_size_mb(conn)
    finally:
        conn.close()

    baseline = None
    if args.baseline_url:
        base_conn = psycopg2.connect(args.baseline_url)
        try:
            baseline = table_counts(base_conn)
        finally:
            base_conn.close()

    print(f"{'TABLE':<28} {'BASELINE':>10} {'NEW':>10}")
    for table in TABLES:
        before = baseline[table] if baseline else "—"
        print(f"{table:<28} {before:>10} {new[table]:>10}")
    print(f"Database size: {size_mb:.0f} MB (limit {args.storage_limit_mb:.0f} MB)")

    problems = compare_counts(new, baseline, args.max_shrink)
    storage_problem, storage_warning = check_storage(size_mb, args.storage_limit_mb)
    if storage_problem:
        problems.append(storage_problem)
    if storage_warning:
        # ::warning:: shows as an annotation on a GitHub Actions run.
        print(f"::warning::{storage_warning}")

    for problem in problems:
        print(f"::error::{problem}")
    if problems:
        print(f"\n{len(problems)} check(s) failed; do not promote this database.", file=sys.stderr)
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
