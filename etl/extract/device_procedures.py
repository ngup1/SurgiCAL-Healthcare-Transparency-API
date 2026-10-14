"""
Procedure-to-Device Mapper

Links devices to the procedures they are used in, from a curated list of FDA
product types per CPT code (etl/reference/procedure_product_codes.csv): for
example knee replacement (27447) uses knee prostheses (JWH, OIY, MBH, KRO) and
bone cement (LOD). No public dataset makes this link, so the list is kept by hand.

Populates the `procedure_product_codes` and `device_procedure_map` tables.
"""
from __future__ import annotations

import csv

from etl.common.config import ETL_DIR, get_logger
from etl.common.db import execute, execute_query, replace_rows

logger = get_logger(__name__)

CURATED_MAP_FILE = ETL_DIR / "reference" / "procedure_product_codes.csv"


def load_curated_map() -> list[dict]:
    """Rows of the curated CSV as {cpt, product_code, usage_type}."""
    with open(CURATED_MAP_FILE, newline="") as fh:
        return [
            {key: row[key].strip() for key in ("cpt", "product_code", "usage_type")}
            for row in csv.DictReader(fh)
        ]


def run() -> int:
    """
    Load the curated map and rebuild device_procedure_map from it.

    Returns:
        Number of device-procedure links.
    """
    curated = load_curated_map()
    known_cpts = {r["code"] for r in execute_query("SELECT code FROM cpt_codes")}
    rows = [r for r in curated if r["cpt"] in known_cpts]
    missing = sorted({r["cpt"] for r in curated} - known_cpts)
    if missing:
        logger.warning(f"Curated map names CPT codes that are not in cpt_codes: {', '.join(missing)}")

    replace_rows("procedure_product_codes", rows, "TRUE", ())

    without_devices = execute_query(
        """
        SELECT DISTINCT ppc.product_code FROM procedure_product_codes ppc
        WHERE NOT EXISTS (SELECT 1 FROM devices d WHERE d.fda_product_code = ppc.product_code)
        ORDER BY 1
        """
    )
    if without_devices:
        codes = ", ".join(r["product_code"] for r in without_devices)
        logger.warning(f"Curated product codes with no devices loaded: {codes}")

    # Rebuild in one statement: links for pairs no longer curated go away, the rest are refreshed.
    execute(
        """
        WITH removed AS (
          DELETE FROM device_procedure_map m
          WHERE NOT EXISTS (
            SELECT 1 FROM devices d
            JOIN procedure_product_codes ppc ON ppc.product_code = d.fda_product_code
            WHERE d.id = m.device_id AND ppc.cpt = m.cpt
          )
        )
        INSERT INTO device_procedure_map (device_id, cpt, usage_type)
        SELECT d.id, ppc.cpt, ppc.usage_type
        FROM procedure_product_codes ppc
        JOIN devices d ON d.fda_product_code = ppc.product_code
        ON CONFLICT (device_id, cpt) DO UPDATE SET usage_type = EXCLUDED.usage_type
        """
    )
    count = execute_query("SELECT count(*) AS n FROM device_procedure_map")[0]["n"]
    logger.info(f"Linked devices to procedures: {count} links from {len(rows)} curated pairs")
    return count


if __name__ == "__main__":
    run()
