#!/usr/bin/env python3
"""
SurgiCAL ETL Pipeline Runner

Runs all ETL steps in dependency order with per-step timing and a summary.

Usage:
    python -m etl.run_pipeline                  # full pipeline
    python -m etl.run_pipeline --steps hospitals providers
    python -m etl.run_pipeline --skip trilliant oria
    python -m etl.run_pipeline --mrf-limit 10   # test with 10 MRF files

Step execution order (dependencies respected):
  1.  hospitals          -- CMS Hospital General Info -> hospitals table
  2.  cpt_codes          -- CMS Physician Fee Schedule seed -> cpt_codes table
  3.  providers          -- NPPES NPI registry -> providers table
  4.  utilization        -- CMS Medicare utilization -> provider_metrics (volume)
  5.  hospital_quality   -- CMS quality ratings -> hospital_quality table
  6.  mrf_links_scrape   -- hospitalpricingfiles.org scraper -> hospital_mrf_links table
  8.  fda_devices        -- openFDA 510k/PMA -> devices table
  9.  fda_maude          -- openFDA MAUDE -> device_adverse_events table
  10. fda_recalls        -- openFDA recalls -> device_recalls table
  11. mrf_prices         -- Parse hospital MRF files -> prices table
  12. trilliant          -- Trilliant Delta Sharing -> provider enrichment (optional)
  13. oria               -- Oria Trilliant S3 -> prices table (optional, needs AWS)
"""
from __future__ import annotations

import argparse
import importlib
import sys
import time
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Step registry
# ---------------------------------------------------------------------------

@dataclass
class Step:
    name: str
    module: str
    run_kwargs: dict[str, Any] = field(default_factory=dict)
    optional: bool = False       # if True, failure doesn't affect exit code
    requires_env: str = ""       # env var that must be set to run this step


STEPS: list[Step] = [
    Step("hospitals",        "etl.extract.cms_hospitals"),
    Step("cpt_codes",        "etl.extract.cpt_fee_schedule"),
    Step("providers",        "etl.extract.cms_physicians"),
    Step("utilization",      "etl.extract.cms_utilization"),
    Step("hospital_quality", "etl.extract.cms_hospital_quality"),
    Step("mrf_links_scrape", "etl.extract.hospitalpricingfiles_scraper", optional=True),
    Step("fda_devices",      "etl.extract.fda_devices"),
    Step("fda_maude",        "etl.extract.fda_maude"),
    Step("fda_recalls",      "etl.extract.fda_recalls"),
    Step("mrf_prices",       "etl.extract.mrf_parser"),
    Step("trilliant",        "etl.enrich.trilliant",   optional=True,
         requires_env="TRILLIANT_SHARE_FILE"),
    Step("oria",             "etl.extract.oria_prices", optional=True,
         requires_env="S3_ORIA_BUCKET"),
]

STEP_NAMES = [s.name for s in STEPS]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _check_env(var: str) -> bool:
    import os
    val = os.getenv(var, "")
    return bool(val.strip())


def _run_step(step: Step, extra_kwargs: dict[str, Any]) -> tuple[str, float, int | None]:
    """
    Import and call step.module.run(**kwargs).

    Returns (status, elapsed_seconds, count_or_None).
    status is one of: 'ok', 'skipped', 'failed'.
    """
    if step.requires_env and not _check_env(step.requires_env):
        return "skipped", 0.0, None

    kwargs = {**step.run_kwargs, **extra_kwargs.get(step.name, {})}

    try:
        mod = importlib.import_module(step.module)
        t0 = time.monotonic()
        count = mod.run(**kwargs)
        elapsed = time.monotonic() - t0
        return "ok", elapsed, count
    except Exception as exc:
        elapsed = time.monotonic() - t0 if "t0" in dir() else 0.0
        print(f"\n  ERROR in {step.name}: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        return "failed", elapsed, None


def _fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s}s"


def _print_summary(results: list[tuple[str, str, float, int | None]]) -> None:
    """Print a tidy results table."""
    print("\n" + "=" * 62)
    print(f"{'STEP':<20} {'STATUS':<10} {'TIME':<10} {'RECORDS':>10}")
    print("-" * 62)
    for name, status, elapsed, count in results:
        icon = {"ok": "\u2713", "failed": "\u2717", "skipped": "\u2013"}.get(status, "?")
        count_str = str(count) if count is not None else "\u2014"
        time_str = _fmt_duration(elapsed) if elapsed else "\u2014"
        print(f"{name:<20} {icon} {status:<8} {time_str:<10} {count_str:>10}")
    print("=" * 62)


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
        help="Limit MRF files parsed in this run (useful for testing)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Pass dry_run=True to enrichment steps (no DB writes for those steps)",
    )
    args = parser.parse_args()

    # Determine which steps to run
    selected = args.steps or STEP_NAMES
    skip_set = set(args.skip or [])
    active_steps = [s for s in STEPS if s.name in selected and s.name not in skip_set]

    # Extra kwargs per step
    extra: dict[str, dict] = {
        "hospitals":        {"state": args.state},
        "providers":        {"state": args.state},
        "utilization":      {},
        "hospital_quality": {"state": args.state},
        "mrf_links_scrape": {"state": args.state},
        "mrf_prices":       {"state": args.state, "limit": args.mrf_limit},
        "trilliant":        {"dry_run": args.dry_run},
    }

    print(f"SurgiCAL ETL Pipeline -- {len(active_steps)} steps")
    print(f"State: {args.state}  |  MRF limit: {args.mrf_limit or 'all'}")
    print("-" * 62)

    results: list[tuple[str, str, float, int | None]] = []
    any_required_failed = False

    for step in active_steps:
        print(f"\n\u25b6  {step.name} ...")
        status, elapsed, count = _run_step(step, extra)

        if status == "skipped":
            reason = f"(requires {step.requires_env})" if step.requires_env else ""
            print(f"   \u2014 skipped {reason}")
        elif status == "ok":
            print(f"   \u2713 done in {_fmt_duration(elapsed)} -- {count} records")
        else:
            print(f"   \u2717 FAILED after {_fmt_duration(elapsed)}")
            if not step.optional:
                any_required_failed = True

        results.append((step.name, status, elapsed, count))

    _print_summary(results)

    if any_required_failed:
        print("\nPipeline completed with errors.", file=sys.stderr)
        return 1

    print("\nPipeline completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
