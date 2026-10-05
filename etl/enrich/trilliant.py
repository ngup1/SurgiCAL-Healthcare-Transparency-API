"""
Trilliant Health Provider Enrichment -- Delta Sharing Edition

Enriches provider records with data from the Trilliant National Provider Directory,
distributed via Databricks Delta Sharing (Marketplace).

Source: Databricks Marketplace -- Trilliant Health National Provider Directory
Access: .share credentials file set via TRILLIANT_SHARE_FILE env var
Library: delta-sharing (open source, no Databricks workspace required)

Updates:
  providers         -- medical_school, graduation_year
  provider_metrics  -- trilliant_specialty, trilliant_active, patient_demographics
  provider_affiliations -- if affiliation columns are present in the Delta table

Note: This module is enrichment-only -- it loads Trilliant data for providers
already in our providers table. If you need to bootstrap providers from
Trilliant as the primary source, create extract/trilliant_directory.py
following the same download/filter pattern.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

import delta_sharing
import pandas as pd

from etl.common.config import get_settings, get_logger
from etl.common.db import execute_query, get_db_connection, upsert_batch

logger = get_logger(__name__)

# Maps Trilliant Delta table column names -> our internal field names.
# Verified against actual schema from directory_providers table (2025-02).
# Patient demographics are flat columns assembled into JSONB in build_metrics_records().
FIELD_MAP: dict[str, str] = {
    # providers table
    "provider_medical_school_name":             "medical_school",
    "provider_medical_school_graduation_year":  "graduation_year",
    # provider_metrics table
    "provider_primary_specialty_description":   "trilliant_specialty",
    "provider_specialty_classification":        "specialty_classification",
    "active_provider":                          "trilliant_active",
    # patient demographics (flat columns -- assembled into JSONB dict)
    "panel_median_age":         "demo_median_age",
    "panel_percent_age_0_19":   "demo_pct_0_19",
    "panel_percent_age_20_44":  "demo_pct_20_44",
    "panel_percent_age_45_64":  "demo_pct_45_64",
    "panel_percent_age_65_84":  "demo_pct_65_84",
    "panel_percent_age_85_plus": "demo_pct_85_plus",
    "panel_percent_female":     "demo_pct_female",
    "panel_percent_male":       "demo_pct_male",
}

# Demographic internal field names that get assembled into the patient_demographics JSONB
DEMO_FIELDS = {
    "demo_median_age", "demo_pct_0_19", "demo_pct_20_44", "demo_pct_45_64",
    "demo_pct_65_84", "demo_pct_85_plus", "demo_pct_female", "demo_pct_male",
}


def build_table_url(settings) -> str:
    """Construct the Delta Sharing table URL from settings."""
    profile = settings.trilliant_share_file
    if not profile or not Path(profile).exists():
        raise FileNotFoundError(
            f"Delta Sharing profile file not found: {profile!r}. "
            "Set TRILLIANT_SHARE_FILE to the path of your .share credentials file."
        )
    return f"{profile}#{settings.trilliant_share_name}.{settings.trilliant_schema_name}.{settings.trilliant_table_name}"


def check_token_expiry(share_file: str) -> None:
    """
    Read the .share JSON and warn if the bearer token has already expired.

    Does not raise -- the caller decides whether to abort.
    """
    try:
        data = json.loads(Path(share_file).read_text())
        exp_str = data.get("expirationTime", "")
        if not exp_str:
            return
        exp = datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        if exp < now:
            delta = now - exp
            days = delta.days
            logger.warning(
                f"Trilliant bearer token EXPIRED {days} day(s) ago ({exp_str}). "
                "Download will fail. Refresh your .share credentials file from "
                "the Databricks Marketplace listing."
            )
        else:
            remaining = exp - now
            logger.info(f"Trilliant token valid for {remaining.days} more day(s) (expires {exp_str})")
    except Exception as exc:
        logger.warning(f"Could not check token expiry: {exc}")


def discover_tables(share_file: str) -> list[str]:
    """
    List all Delta Sharing tables available under this .share credential.

    Run this once after getting a new .share file to find the correct
    share / schema / table names for TRILLIANT_SHARE_NAME, TRILLIANT_SCHEMA_NAME,
    and TRILLIANT_TABLE_NAME.

    Returns a list of fully-qualified table URLs:
        <share_file>#<share>.<schema>.<table>

    Usage:
        python -m etl.enrich.trilliant --discover
    """
    client = delta_sharing.SharingClient(share_file)
    tables = client.list_all_tables()

    urls = []
    for t in tables:
        url = f"{share_file}#{t.share}.{t.schema}.{t.name}"
        urls.append(url)

    if urls:
        logger.info(f"Discovered {len(urls)} table(s):")
        for url in urls:
            logger.info(f"  {url}")
        logger.info(
            "\nSet these env vars in etl/.env:\n"
            f"  TRILLIANT_SHARE_FILE={share_file}\n"
            f"  TRILLIANT_SHARE_NAME=<share from above>\n"
            f"  TRILLIANT_SCHEMA_NAME=<schema from above>\n"
            f"  TRILLIANT_TABLE_NAME=<table from above>"
        )
    else:
        logger.warning("No tables found. Check your credentials and token expiry.")

    return urls


def inspect_schema(table_url: str) -> list[str]:
    """
    Load 5 rows from the Delta table and log all column names.

    Run this once interactively before the first full run to verify actual
    Trilliant column names and update FIELD_MAP if needed.

    Usage:
        python -m etl.enrich.trilliant --inspect
    """
    logger.info("Loading 5 rows for schema inspection...")
    sample = delta_sharing.load_as_pandas(table_url, limit=5)
    cols = list(sample.columns)

    logger.info(f"Delta table columns ({len(cols)} total):\n  {cols}")
    if len(sample) > 0:
        logger.info(f"Sample row:\n  {sample.iloc[0].to_dict()}")

    matched = {k: v for k, v in FIELD_MAP.items() if k in cols}
    unmatched = {k: v for k, v in FIELD_MAP.items() if k not in cols}
    logger.info(f"FIELD_MAP matched keys: {list(matched.keys())}")
    if unmatched:
        logger.warning(
            f"FIELD_MAP unmatched keys (update FIELD_MAP if these are expected fields): "
            f"{list(unmatched.keys())}"
        )
    return cols


def resolve_field_map(actual_columns: list[str]) -> dict[str, str]:
    """
    Build a resolved mapping {actual_col -> internal_field} from FIELD_MAP
    and the real DataFrame columns.

    When multiple FIELD_MAP keys map to the same target, the first match wins.
    Logs a warning for any internal fields that couldn't be resolved.
    """
    col_set = set(actual_columns)
    resolved: dict[str, str] = {}
    claimed_targets: set[str] = set()

    for source_col, target_field in FIELD_MAP.items():
        if source_col in col_set and target_field not in claimed_targets:
            resolved[source_col] = target_field
            claimed_targets.add(target_field)

    # Warn about missing fields (demo fields and affiliation fields are optional)
    optional_targets = {"affiliation_ccns", "affiliation_locations"} | DEMO_FIELDS
    required_targets = set(FIELD_MAP.values()) - optional_targets
    missing = required_targets - claimed_targets
    if missing:
        logger.warning(
            f"Could not map these schema fields from the Delta table: {missing}. "
            "Run --inspect and update FIELD_MAP."
        )

    logger.info(f"Resolved {len(resolved)} column mappings: {resolved}")
    return resolved


def get_ca_surgical_npis() -> set[str]:
    """Return NPIs for all CA providers currently in our database."""
    rows = execute_query("SELECT npi FROM providers WHERE state = 'CA'")
    return {r["npi"] for r in rows}


def get_ca_hospitals() -> list[dict]:
    """Return CA hospitals with ccn, name, city, zip for affiliation matching."""
    return execute_query(
        "SELECT ccn, name, city, zip FROM hospitals WHERE state = 'CA'"
    )


def _normalize_name(text: str) -> str:
    """Lowercase, collapse punctuation/whitespace for name similarity matching."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def load_directory(table_url: str, target_npis: set[str]) -> pd.DataFrame:
    """
    Download the Trilliant Delta Sharing table and filter to target NPIs.

    Uses convert_in_batches=True to read Parquet fragments one at a time,
    reducing peak RAM. After the full load, immediately filters to target_npis
    to bring the working set down from ~2.9M rows to ~5-8K rows.
    """
    logger.info("Downloading Trilliant National Provider Directory via Delta Sharing...")
    logger.info(f"Will filter to {len(target_npis)} CA provider NPIs after download")

    df = delta_sharing.load_as_pandas(table_url, convert_in_batches=True)
    logger.info(f"Downloaded {len(df):,} total rows")

    # Find the NPI column -- handle common name variants
    npi_col = None
    for candidate in ("provider_npi", "npi", "NPI", "Npi", "national_provider_identifier"):
        if candidate in df.columns:
            npi_col = candidate
            break

    if npi_col is None:
        raise ValueError(
            f"Cannot find NPI column in Delta table. Actual columns: {list(df.columns)}. "
            "Add the correct column name to the candidates list in load_directory()."
        )

    df[npi_col] = df[npi_col].astype(str).str.strip()
    df_filtered = df[df[npi_col].isin(target_npis)].copy()

    logger.info(
        f"Filtered to {len(df_filtered):,} rows matching CA providers "
        f"({len(target_npis) - len(df_filtered)} NPIs not found in Trilliant directory)"
    )

    if npi_col != "npi":
        df_filtered = df_filtered.rename(columns={npi_col: "npi"})

    return df_filtered


def _is_missing(val) -> bool:
    """Return True if a value should be treated as absent."""
    if isinstance(val, (list, dict)):
        return False
    try:
        if pd.isna(val):
            return True
    except (TypeError, ValueError):
        pass
    return isinstance(val, str) and val.strip() == ""


def map_row(row: pd.Series, resolved_map: dict[str, str]) -> dict:
    """Convert a DataFrame row to {internal_field: value}, skipping missing values."""
    result = {}
    for source_col, target_field in resolved_map.items():
        val = row.get(source_col)
        if not _is_missing(val):
            result[target_field] = val
    return result


def build_providers_records(df: pd.DataFrame, resolved_map: dict[str, str]) -> list[dict]:
    """
    Build upsert records for the providers table.

    Only touches medical_school and graduation_year -- NPPES identity fields
    (name, specialty, city, state) are not overwritten.
    """
    records = []
    for _, row in df.iterrows():
        mapped = map_row(row, resolved_map)
        npi = str(row.get("npi", "")).strip()
        if not npi:
            continue

        record: dict = {"npi": npi}

        if "medical_school" in mapped:
            record["medical_school"] = str(mapped["medical_school"]).strip() or None

        if "graduation_year" in mapped:
            try:
                record["graduation_year"] = int(float(mapped["graduation_year"]))
            except (ValueError, TypeError):
                pass

        if len(record) > 1:  # has more than just npi
            records.append(record)

    return records


def build_metrics_records(df: pd.DataFrame, resolved_map: dict[str, str]) -> list[dict]:
    """Build upsert records for the provider_metrics table."""
    now = datetime.now(timezone.utc)
    records = []

    for _, row in df.iterrows():
        mapped = map_row(row, resolved_map)
        npi = str(row.get("npi", "")).strip()
        if not npi:
            continue

        record: dict = {"npi": npi, "last_refreshed": now}

        if "trilliant_specialty" in mapped:
            record["trilliant_specialty"] = str(mapped["trilliant_specialty"]).strip() or None

        if "specialty_classification" in mapped:
            record["specialty_classification"] = str(mapped["specialty_classification"]).strip() or None

        if "trilliant_active" in mapped:
            val = mapped["trilliant_active"]
            if isinstance(val, bool):
                record["trilliant_active"] = val
            elif isinstance(val, str):
                record["trilliant_active"] = val.strip().lower() in ("true", "1", "yes", "active")
            else:
                try:
                    record["trilliant_active"] = bool(val)
                except (ValueError, TypeError):
                    pass

        # Assemble flat panel columns into a single patient_demographics JSONB dict
        demo = {}
        demo_key_map = {
            "demo_median_age":   "median_age",
            "demo_pct_0_19":     "pct_age_0_19",
            "demo_pct_20_44":    "pct_age_20_44",
            "demo_pct_45_64":    "pct_age_45_64",
            "demo_pct_65_84":    "pct_age_65_84",
            "demo_pct_85_plus":  "pct_age_85_plus",
            "demo_pct_female":   "pct_female",
            "demo_pct_male":     "pct_male",
        }
        for internal_field, json_key in demo_key_map.items():
            if internal_field in mapped:
                try:
                    demo[json_key] = float(mapped[internal_field])
                except (ValueError, TypeError):
                    pass
        if demo:
            record["patient_demographics"] = json.dumps(demo)

        if len(record) > 2:  # has more than npi + last_refreshed
            records.append(record)

    return records


def build_affiliations_records(
    df: pd.DataFrame,
    resolved_map: dict[str, str],
    ca_ccns: set[str],
) -> list[dict]:
    """
    Build upsert records for provider_affiliations from CCN affiliation columns.

    Returns an empty list if no affiliation columns are present -- these are
    optional in the Trilliant free tier.
    """
    affiliation_targets = {"affiliation_ccns", "affiliation_locations"}
    if not any(t in resolved_map.values() for t in affiliation_targets):
        logger.debug("No affiliation columns in Delta table -- skipping affiliation load")
        return []

    records = []
    for _, row in df.iterrows():
        mapped = map_row(row, resolved_map)
        npi = str(row.get("npi", "")).strip()
        if not npi:
            continue

        ccns = mapped.get("affiliation_ccns", [])
        if isinstance(ccns, str):
            try:
                ccns = json.loads(ccns)
            except json.JSONDecodeError:
                ccns = [c.strip() for c in ccns.split(",") if c.strip()]

        for ccn in (ccns or []):
            ccn = str(ccn).strip()
            if ccn and ccn in ca_ccns:
                records.append({"npi": npi, "ccn": ccn, "is_primary": False})

    return records


def build_affiliations_by_name(
    df: pd.DataFrame,
    ca_hospitals: list[dict],
    similarity_threshold: float = 0.75,
) -> list[dict]:
    """
    Build provider_affiliations records by fuzzy-matching Trilliant practice
    names against our hospitals table.

    Trilliant does not expose CCNs in the free-tier directory, so we match on
    provider_affiliated_practice_1_name (falling back to primary_organization_name)
    against hospital names grouped by city. A small zip-code bonus is applied
    when both sides have a zip to reward geographic proximity.

    Args:
        ca_hospitals: rows from get_ca_hospitals() -- {ccn, name, city, zip}
        similarity_threshold: minimum SequenceMatcher ratio to accept a match
                              (0.75 works well; lower = more matches but noisier)
    """
    # Build city -> [{ccn, norm_name, zip}] index for fast candidate lookup
    city_index: dict[str, list[dict]] = {}
    for h in ca_hospitals:
        key = _normalize_name(h.get("city") or "")
        city_index.setdefault(key, []).append({
            "ccn":       h["ccn"],
            "norm_name": _normalize_name(h["name"]),
            "zip":       (h.get("zip") or "")[:5],
        })
    all_hospitals = [h for bucket in city_index.values() for h in bucket]

    records: list[dict] = []
    matched = unmatched = 0

    for _, row in df.iterrows():
        npi = str(row.get("npi", "")).strip()
        if not npi:
            continue

        # Prefer practice name, fall back to primary org name
        raw_name = row.get("provider_affiliated_practice_1_name")
        if _is_missing(raw_name):
            raw_name = row.get("primary_organization_name")
        if _is_missing(raw_name):
            unmatched += 1
            continue

        practice_state = str(row.get("provider_affiliated_practice_1_state") or "").strip().upper()
        if practice_state and practice_state != "CA":
            continue

        practice_city = _normalize_name(str(row.get("provider_affiliated_practice_1_city") or ""))
        practice_zip  = str(row.get("provider_affiliated_practice_1_zip_code") or "")[:5]
        norm_practice = _normalize_name(str(raw_name))

        # Prefer same-city candidates; fall back to all CA hospitals
        candidates = city_index.get(practice_city) or all_hospitals

        best_ccn   = None
        best_score = 0.0
        for candidate in candidates:
            score = SequenceMatcher(None, norm_practice, candidate["norm_name"]).ratio()
            if practice_zip and candidate["zip"] and practice_zip == candidate["zip"]:
                score = min(1.0, score + 0.05)
            if score > best_score:
                best_score = score
                best_ccn   = candidate["ccn"]

        if best_score >= similarity_threshold and best_ccn:
            records.append({"npi": npi, "ccn": best_ccn, "is_primary": False})
            matched += 1
        else:
            unmatched += 1

    logger.info(
        f"Affiliation name-match: {matched} linked, {unmatched} unmatched "
        f"(threshold={similarity_threshold})"
    )
    return records


def run(dry_run: bool = False, affiliation_threshold: float = 0.75) -> int:
    """
    Run Trilliant enrichment using the bulk Delta Sharing download.

    Downloads the full Trilliant National Provider Directory, filters to CA
    surgical providers already in our database, and upserts enrichment data.

    Args:
        dry_run: If True, load and map data but skip all DB writes.
                 Use this to verify FIELD_MAP before the first production run.

    Returns:
        Number of providers enriched (or matched in dry_run mode).
    """
    settings = get_settings()

    if not settings.trilliant_share_file:
        logger.error("TRILLIANT_SHARE_FILE not set. Skipping enrichment.")
        return 0

    logger.info(f"Starting Trilliant enrichment via Delta Sharing (dry_run={dry_run})")

    check_token_expiry(settings.trilliant_share_file)
    table_url = build_table_url(settings)

    ca_npis = get_ca_surgical_npis()
    if not ca_npis:
        logger.warning("No CA providers in database. Run cms_physicians.py first.")
        return 0
    logger.info(f"Enriching up to {len(ca_npis)} CA providers")

    df = load_directory(table_url, ca_npis)
    if df.empty:
        logger.warning("No matching providers found in Trilliant directory")
        return 0

    resolved_map = resolve_field_map(list(df.columns))

    provider_records = build_providers_records(df, resolved_map)
    metrics_records = build_metrics_records(df, resolved_map)
    ca_hospitals = get_ca_hospitals()
    affiliation_records = build_affiliations_by_name(df, ca_hospitals, affiliation_threshold)

    logger.info(
        f"Built {len(provider_records)} provider updates, "
        f"{len(metrics_records)} metrics updates, "
        f"{len(affiliation_records)} affiliation records"
    )

    if dry_run:
        logger.info("DRY RUN -- no database writes.")
        if provider_records:
            logger.info(f"Sample provider record: {provider_records[0]}")
        if metrics_records:
            logger.info(f"Sample metrics record: {metrics_records[0]}")
        if affiliation_records:
            logger.info(f"Sample affiliation record: {affiliation_records[0]}")
        return len(metrics_records)

    if provider_records:
        upsert_batch(
            table="providers",
            records=provider_records,
            conflict_columns=["npi"],
            update_columns=["medical_school", "graduation_year"],
        )

    if metrics_records:
        upsert_batch(
            table="provider_metrics",
            records=metrics_records,
            conflict_columns=["npi"],
            update_columns=[
                "trilliant_specialty",
                "specialty_classification",
                "trilliant_active",
                "patient_demographics",
                "last_refreshed",
            ],
        )

    if affiliation_records:
        upsert_batch(
            table="provider_affiliations",
            records=affiliation_records,
            conflict_columns=["npi", "ccn"],
            update_columns=[],
        )

    enriched = len(metrics_records)
    logger.info(f"Enriched {enriched} providers via Trilliant Delta Sharing")
    return enriched


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Trilliant Health provider enrichment")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Load and map data without writing to the database",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="List all Delta Sharing tables in the .share file and print env var config",
    )
    parser.add_argument(
        "--inspect",
        action="store_true",
        help="Print Delta table schema (5 rows) and exit -- run this first to verify FIELD_MAP",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.75,
        metavar="N",
        help="Name similarity threshold for affiliation matching (default: 0.75)",
    )
    args = parser.parse_args()

    _settings = get_settings()

    if args.discover:
        if not _settings.trilliant_share_file:
            print("ERROR: TRILLIANT_SHARE_FILE not set. Set it in etl/.env first.")
            sys.exit(1)
        discover_tables(_settings.trilliant_share_file)
    elif args.inspect:
        _url = build_table_url(_settings)
        inspect_schema(_url)
    else:
        run(dry_run=args.dry_run, affiliation_threshold=args.threshold)
