"""
Empty every table the pipeline loads, for a first real load into a database
that holds the mock seed data (real and mock rows must not mix).

    python -m etl.reset --yes

Run it only against a database made for the load (a Neon branch), never the one
the API is serving: the tables stay empty until the pipeline has run.
"""
from __future__ import annotations

import argparse
import sys

from etl.common.config import get_logger, get_settings
from etl.common.db import execute

logger = get_logger(__name__)

# Children first is not needed with CASCADE, but the list is the full set the pipeline owns.
TABLES = [
    "prices", "hospital_mrf_links", "device_adverse_events", "device_recalls", "device_procedure_map",
    "device_prices", "procedure_product_codes", "devices", "provider_procedure_volumes", "provider_metrics",
    "provider_affiliations", "providers", "hospital_quality", "hospitals", "cpt_codes", "ca_places",
    "payer_aliases",
]


def run() -> None:
    execute(f"TRUNCATE {', '.join(TABLES)} CASCADE")
    logger.info(f"Emptied {len(TABLES)} tables")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Empty the tables the ETL loads")
    parser.add_argument("--yes", action="store_true", help="Required: confirms that the tables should be emptied")
    args = parser.parse_args()
    if not args.yes:
        target = get_settings().database_url.rsplit("@", 1)[-1] or get_settings().db_host
        print(f"This empties every ETL table in {target}. Re-run with --yes to do it.", file=sys.stderr)
        sys.exit(2)
    run()
