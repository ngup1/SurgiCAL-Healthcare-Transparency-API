"""Step selection, dependency gating and the empty-result rule of the pipeline runner."""

import sys
import types

from etl.run_pipeline import STEPS_BY_NAME, Result, _run_step, blocked_reason, select_steps


def counts(**rows):
    """A stand-in for the table row counts, defaulting to 100 rows."""
    return lambda tables: {t: rows.get(t, 100) for t in tables}


def test_step_is_blocked_when_a_required_step_failed_in_this_run():
    results = {"hospitals": Result("hospitals", "failed")}
    reason = blocked_reason(STEPS_BY_NAME["hospital_quality"], results, counts())
    assert reason == "hospitals failed"


def test_step_is_blocked_when_a_required_step_loaded_nothing():
    results = {"providers": Result("providers", "empty"), "cpt_codes": Result("cpt_codes", "ok")}
    assert blocked_reason(STEPS_BY_NAME["utilization"], results, counts()) == "providers empty"


def test_step_run_alone_checks_that_required_data_exists():
    step = STEPS_BY_NAME["hospital_quality"]
    assert blocked_reason(step, {}, counts()) is None
    assert "hospitals is empty" in blocked_reason(step, {}, counts(hospitals=0))


def test_blocked_steps_block_their_dependents():
    results = {"fda_devices": Result("fda_devices", "blocked")}
    assert blocked_reason(STEPS_BY_NAME["fda_recalls"], results, counts()) == "fda_devices blocked"


def test_opt_in_steps_run_only_when_named():
    assert "trilliant" not in [s.name for s in select_steps(None, [])]
    assert [s.name for s in select_steps(["trilliant"], [])] == ["trilliant"]
    assert "mrf_prices" not in [s.name for s in select_steps(None, ["mrf_prices"])]


def _fake_step(monkeypatch, run):
    module = types.ModuleType("etl_fake_step")
    module.run = run
    monkeypatch.setitem(sys.modules, "etl_fake_step", module)
    return STEPS_BY_NAME["hospitals"].__class__("fake", "etl_fake_step", ())


def test_a_step_that_loads_nothing_is_not_ok(monkeypatch):
    result = _run_step(_fake_step(monkeypatch, lambda: 0), {})
    assert result.status == "empty"


def test_a_step_gets_only_the_options_it_declares(monkeypatch):
    seen = {}

    def run(state="CA"):
        seen["state"] = state
        return 3

    result = _run_step(_fake_step(monkeypatch, run), {"state": "NV", "limit": 5, "dry_run": True})
    assert (result.status, result.records, seen) == ("ok", 3, {"state": "NV"})


def test_an_exception_fails_the_step(monkeypatch):
    def run():
        raise RuntimeError("source is down")

    result = _run_step(_fake_step(monkeypatch, run), {})
    assert result.status == "failed" and "source is down" in result.error


def test_trilliant_is_skipped_without_its_share_file(monkeypatch):
    monkeypatch.delenv("TRILLIANT_SHARE_FILE", raising=False)
    assert _run_step(STEPS_BY_NAME["trilliant"], {}).status == "skipped"
