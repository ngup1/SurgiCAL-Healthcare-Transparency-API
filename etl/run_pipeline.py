#!/usr/bin/env python3
"""
SurgiCAL ETL Pipeline Runner

Runs ETL steps in dependency order with per-step timing and a summary.

Usage:
    python -m etl.run_pipeline                  # every step except opt-in ones
    python -m etl.run_pipeline --steps hospitals providers
    python -m etl.run_pipeline --skip mrf_prices
    python -m etl.run_pipeline --mrf-limit 10   # test with 10 price files
    python -m etl.run_pipeline --list           # show steps and what they need

Steps, in order (a step runs only if the steps it needs have data):
  places             Census Gazetteer -> ca_places (cities, counties, ZIP centre points)
  hospitals          CMS Hospital General Information -> hospitals
  cpt_codes          CMS Physician Fee Schedule -> cpt_codes
  providers          NPPES NPI registry -> providers
  utilization        CMS Medicare utilization -> provider_metrics, provider_procedure_volumes
  hospital_quality   CMS quality measures -> hospital_quality
  fda_devices        openFDA 510(k)/PMA -> devices
  device_procedures  curated CPT/product-code map -> device_procedure_map
  fda_recalls        openFDA recalls -> device_recalls
  fda_maude          openFDA adverse events -> device_adverse_events
  mrf_links          hospitals' cms-hpt.txt files -> hospital_mrf_links
  mrf_prices         hospital price files -> prices
  trilliant          Trilliant Delta Sharing -> provider enrichment (opt-in: name it in --steps)

A step that loads nothing counts as a failure: an empty result has always meant a
broken source, not an empty one. Each step's outcome is recorded in etl_runs.
"""
from __future__ import annotations

import argparse
import importlib
import inspect
import json
import sys
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

# Imported first: it loads etl/.env, which later imports and steps read.
from etl.common.config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Step registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    name: str
    module: str
    tables: tuple[str, ...]  # tables the step fills; used for gating and run history
    requires: tuple[str, ...] = ()  # steps whose data must exist before this one runs
    optional: bool = False  # if True, failure doesn't affect exit code
    opt_in: bool = False  # if True, runs only when named in --steps
    requires_env: str = ""  # env var that must be set to run this step


STEPS: list[Step] = [
    Step("places", "etl.extract.census_places", ("ca_places",)),
    Step("hospitals", "etl.extract.cms_hospitals", ("hospitals",), requires=("places",)),
    Step("cpt_codes", "etl.extract.cpt_fee_schedule", ("cpt_codes",)),
    Step("providers", "etl.extract.cms_physicians", ("providers",), requires=("places",)),
    Step(
        "utilization",
        "etl.extract.cms_utilization",
        ("provider_metrics", "provider_procedure_volumes"),
        requires=("providers", "cpt_codes"),
    ),
    Step("hospital_quality", "etl.extract.cms_hospital_quality", ("hospital_quality",), requires=("hospitals",)),
    Step("fda_devices", "etl.extract.fda_devices", ("devices",)),
    Step(
        "device_procedures",
        "etl.extract.device_procedures",
        ("procedure_product_codes", "device_procedure_map"),
        requires=("fda_devices", "cpt_codes"),
    ),
    Step("fda_recalls", "etl.extract.fda_recalls", ("device_recalls",), requires=("fda_devices",)),
    Step("fda_maude", "etl.extract.fda_maude", ("device_adverse_events",), requires=("fda_devices",)),
    Step("mrf_links", "etl.extract.mrf_links", ("hospital_mrf_links",), requires=("hospitals",)),
    Step("mrf_prices", "etl.extract.mrf_parser", ("prices",), requires=("mrf_links", "cpt_codes")),
    Step(
        "trilliant",
        "etl.enrich.trilliant",
        (),
        requires=("providers",),
        optional=True,
        opt_in=True,
        requires_env="TRILLIANT_SHARE_FILE",
    ),
]

STEP_NAMES = [s.name for s in STEPS]
STEPS_BY_NAME = {s.name: s for s in STEPS}

# 'ok' is the only status that lets dependent steps run.
STATUS_ICONS = {"ok": "✓", "empty": "✗", "failed": "✗", "blocked": "✗", "skipped": "–"}


@dataclass
class Result:
    step: str
    status: str
    elapsed: float = 0.0
    records: int | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _check_env(var: str) -> bool:
    import os

    return bool(os.getenv(var, "").strip())


def _table_counts(tables: tuple[str, ...]) -> dict[str, int]:
    from etl.common.db import execute_query

    # Table names come from the STEPS registry above, never from input.
    return {t: execute_query(f"SELECT count(*) AS n FROM {t}")[0]["n"] for t in tables}


def blocked_reason(step: Step, results: dict[str, Result], count_tables=_table_counts) -> str | None:
    """
    Why this step can't run, or None if it can.

    A required step that ran in this invocation must have succeeded. One that
    didn't run (because of --steps or --skip) must have left data from an earlier run.
    """
    for name in step.requires:
        required = STEPS_BY_NAME[name]
        if name in results:
            if results[name].status != "ok":
                return f"{name} {results[name].status}"
            continue
        empty = [t for t, n in count_tables(required.tables).items() if n == 0]
        if empty:
            return f"{name} has not been loaded ({', '.join(empty)} is empty)"
    return None


def _run_step(step: Step, options: dict[str, Any]) -> Result:
    """
    Import and call step.module.run(), passing the options its signature accepts.
    """
    if step.requires_env and not _check_env(step.requires_env):
        return Result(step.name, "skipped", error=f"requires {step.requires_env}")

    t0 = time.monotonic()
    try:
        mod = importlib.import_module(step.module)
        accepted = inspect.signature(mod.run).parameters
        kwargs = {k: v for k, v in options.items() if k in accepted}
        count = mod.run(**kwargs)
    except Exception as exc:
        print(f"\n  ERROR in {step.name}: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return Result(step.name, "failed", time.monotonic() - t0, error=f"{type(exc).__name__}: {exc}"[:2000])

    elapsed = time.monotonic() - t0
    if not count:
        return Result(step.name, "empty", elapsed, records=0, error="step loaded no records")
    return Result(step.name, "ok", elapsed, records=count)


def _record(run_id: str, step: Step, result: Result, started_at: datetime) -> None:
    """Write the step's outcome to etl_runs. History is best-effort: it never fails a run."""
    from etl.common.db import execute

    try:
        counts = _table_counts(step.tables) if result.status in ("ok", "empty", "failed") else None
        execute(
            """
            INSERT INTO etl_runs (run_id, step, status, records, table_rows, error, started_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                run_id,
                step.name,
                result.status,
                result.records,
                json.dumps(counts) if counts is not None else None,
                result.error,
                started_at,
            ),
        )
    except Exception as exc:
        logger.warning(f"Could not record {step.name} in etl_runs: {exc}")


def _fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s}s"


def _print_summary(results: list[Result]) -> None:
    """Print a tidy results table."""
    print("\n" + "=" * 62)
    print(f"{'STEP':<20} {'STATUS':<10} {'TIME':<10} {'RECORDS':>10}")
    print("-" * 62)
    for r in results:
        icon = STATUS_ICONS.get(r.status, "?")
        count_str = str(r.records) if r.records is not None else "—"
        time_str = _fmt_duration(r.elapsed) if r.elapsed else "—"
        print(f"{r.step:<20} {icon} {r.status:<8} {time_str:<10} {count_str:>10}")
    print("=" * 62)


def select_steps(only: list[str] | None, skip: list[str]) -> list[Step]:
    """Steps to run, in registry order. Opt-in steps run only when named."""
    if only:
        return [s for s in STEPS if s.name in only and s.name not in skip]
    return [s for s in STEPS if not s.opt_in and s.name not in skip]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the SurgiCAL ETL pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--steps",
        nargs="+",
        choices=STEP_NAMES,
        metavar="STEP",
        help=f"Run only these steps (choices: {', '.join(STEP_NAMES)})",
    )
    parser.add_argument(
        "--skip",
        nargs="+",
        choices=STEP_NAMES,
        metavar="STEP",
        default=[],
        help="Skip these steps",
    )
    parser.add_argument(
        "--state",
        default="CA",
        help="State filter for all extractors (default: CA)",
    )
    parser.add_argument(
        "--mrf-limit",
        type=int,
        default=None,
        metavar="N",
        help="Limit price files parsed in this run (useful for testing)",
    )
    parser.add_argument(
        "--mrf-shard",
        default=None,
        metavar="I/N",
        help="Parse only shard I of N of the price files (0-based), to split a load across jobs",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Pass dry_run=True to enrichment steps (no DB writes for those steps)",
    )
    parser.add_argument("--list", action="store_true", help="List the steps and exit")
    args = parser.parse_args()

    if args.list:
        for s in STEPS:
            needs = f"  needs: {', '.join(s.requires)}" if s.requires else ""
            flags = "  (opt-in)" if s.opt_in else ""
            print(f"{s.name:<20} -> {', '.join(s.tables) or 'enrichment'}{needs}{flags}")
        return 0

    active_steps = select_steps(args.steps, args.skip)

    # Each step's run() receives the options it declares as parameters.
    options: dict[str, Any] = {
        "state": args.state.upper(),
        "limit": args.mrf_limit,
        "shard": args.mrf_shard,
        "dry_run": args.dry_run,
    }

    run_id = str(uuid.uuid4())
    print(f"SurgiCAL ETL Pipeline -- {len(active_steps)} steps (run {run_id})")
    print(f"State: {args.state}  |  MRF limit: {args.mrf_limit or 'all'}")
    print("-" * 62)

    results: dict[str, Result] = {}
    any_required_failed = False

    for step in active_steps:
        print(f"\n▶  {step.name} ...")
        started_at = datetime.now(UTC)

        try:
            reason = blocked_reason(step, results)
        except Exception as exc:
            reason = f"could not check requirements: {exc}"
        result = Result(step.name, "blocked", error=reason) if reason else _run_step(step, options)

        if result.status == "skipped":
            print(f"   — skipped ({result.error})")
        elif result.status == "blocked":
            print(f"   ✗ BLOCKED: {result.error}")
        elif result.status == "ok":
            print(f"   ✓ done in {_fmt_duration(result.elapsed)} -- {result.records} records")
        elif result.status == "empty":
            print(f"   ✗ EMPTY after {_fmt_duration(result.elapsed)}: the step loaded nothing")
        else:
            print(f"   ✗ FAILED after {_fmt_duration(result.elapsed)}")

        if result.status in ("failed", "empty", "blocked") and not step.optional:
            any_required_failed = True

        results[step.name] = result
        _record(run_id, step, result, started_at)

    _print_summary(list(results.values()))

    if any_required_failed:
        print("\nPipeline completed with errors.", file=sys.stderr)
        return 1

    print("\nPipeline completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
