"""The checks that gate promoting a refreshed database."""

from etl.checks import REQUIRED, check_storage, compare_counts

FULL = dict.fromkeys(REQUIRED, 1000)


def test_a_table_that_shrinks_by_more_than_the_limit_fails():
    problems = compare_counts({**FULL, "providers": 790}, FULL, max_shrink=0.20)
    assert problems == ["providers shrank by 21% (1000 -> 790 rows)"]


def test_small_shrinkage_and_growth_pass():
    assert compare_counts({**FULL, "providers": 800, "prices": 5000}, FULL, max_shrink=0.20) == []


def test_an_empty_core_table_fails_even_without_a_baseline():
    assert compare_counts({**FULL, "prices": 0}, None, max_shrink=0.20) == ["prices is empty"]


def test_a_table_new_to_the_baseline_is_not_a_shrink():
    assert compare_counts({**FULL, "device_recalls": 10}, {**FULL, "device_recalls": 0}, max_shrink=0.20) == []


def test_storage_limit():
    assert check_storage(500, 1024) == (None, None)
    problem, warning = check_storage(900, 1024)
    assert problem is None and "88%" in warning
    problem, warning = check_storage(1100, 1024)
    assert "over the 1024 MB limit" in problem and warning is None
