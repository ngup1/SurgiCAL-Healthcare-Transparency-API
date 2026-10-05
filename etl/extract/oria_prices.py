"""
Oria Trilliant Price Data Extractor

Loads pre-parsed MRF pricing data from an S3 bucket via AWS Athena.
The data comes from Oria Trilliant's cloud-native query export.

Populates the `prices` table with source='oria_trilliant'.
"""
from __future__ import annotations


import boto3

from etl.common.config import get_settings, get_logger
from etl.common.db import upsert_batch, execute_query

logger = get_logger(__name__)


def get_athena_client():
    """Create a boto3 Athena client."""
    settings = get_settings()
    return boto3.client("athena", region_name=settings.aws_region)


def get_s3_client():
    """Create a boto3 S3 client."""
    settings = get_settings()
    return boto3.client("s3", region_name=settings.aws_region)


def get_valid_cpts() -> set[str]:
    """Get CPT codes that exist in the cpt_codes table."""
    rows = execute_query("SELECT code FROM cpt_codes")
    return {r["code"] for r in rows}


def get_valid_ccns() -> set[str]:
    """Get hospital CCNs that exist in the hospitals table."""
    rows = execute_query("SELECT ccn FROM hospitals WHERE state = 'CA'")
    return {r["ccn"] for r in rows}


def load_from_s3_csv(bucket: str, key: str) -> list[dict]:
    """
    Download and parse a CSV file from S3 containing Oria pricing data.

    Expected columns (adapt based on actual Oria export format):
    - hospital_ccn or ein
    - cpt_code or billing_code
    - payer_name
    - plan_name
    - billing_class (professional/facility)
    - negotiated_rate
    - negotiated_min / negotiated_max
    - cash_rate

    Returns list of price records ready for DB upsert.
    """
    import csv
    from io import StringIO

    s3 = get_s3_client()
    logger.info(f"Downloading s3://{bucket}/{key}")
    obj = s3.get_object(Bucket=bucket, Key=key)
    body = obj["Body"].read().decode("utf-8")

    valid_cpts = get_valid_cpts()
    valid_ccns = get_valid_ccns()

    records = []
    reader = csv.DictReader(StringIO(body))
    for row in reader:
        ccn = (row.get("hospital_ccn") or row.get("ccn") or row.get("ein") or "").strip()
        cpt = (row.get("cpt_code") or row.get("billing_code") or row.get("hcpcs") or "").strip()
        payer = (row.get("payer_name") or row.get("payer") or "").strip()

        if not ccn or not cpt or not payer:
            continue

        # Filter to known CPTs and CA hospitals
        if cpt not in valid_cpts:
            continue
        if ccn not in valid_ccns:
            continue

        record = {
            "cpt": cpt,
            "ccn": ccn,
            "payer": payer,
            "plan_name": (row.get("plan_name") or "").strip(),
            "billing_class": (row.get("billing_class") or "").strip().lower(),
            "cash_price": _safe_numeric(row.get("cash_rate") or row.get("cash_price")),
            "negotiated_rate": _safe_numeric(row.get("negotiated_rate")),
            "negotiated_min": _safe_numeric(row.get("negotiated_min")),
            "negotiated_max": _safe_numeric(row.get("negotiated_max")),
            "source": "oria_trilliant",
            "measure_date": (row.get("effective_date") or row.get("date") or "").strip() or None,
        }
        records.append(record)

    logger.info(f"Parsed {len(records)} price records from S3")
    return records


def _safe_numeric(val) -> float | None:
    if val is None:
        return None
    try:
        v = float(str(val).replace(",", "").replace("$", "").strip())
        return v if v > 0 else None
    except (ValueError, TypeError):
        return None


def run(bucket: str | None = None, key: str | None = None) -> int:
    """
    Run the Oria price extractor.

    Args:
        bucket: S3 bucket name. Defaults to S3_ORIA_BUCKET env var.
        key: S3 object key for the pricing CSV/Parquet.

    Returns:
        Number of prices loaded.
    """
    settings = get_settings()
    bucket = bucket or settings.s3_oria_bucket
    if not bucket or not key:
        logger.error("S3 bucket and key are required. Set S3_ORIA_BUCKET env var and pass key.")
        return 0

    logger.info(f"Starting Oria price extraction from s3://{bucket}/{key}")
    records = load_from_s3_csv(bucket, key)

    if not records:
        logger.warning("No price records found")
        return 0

    count = upsert_batch(
        table="prices",
        records=records,
        conflict_columns=["cpt", "ccn", "payer", "plan_name", "billing_class"],
    )

    logger.info(f"Loaded {count} prices from Oria Trilliant")
    return count


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3:
        run(bucket=sys.argv[1], key=sys.argv[2])
    else:
        print("Usage: python -m etl.extract.oria_prices <bucket> <key>")
