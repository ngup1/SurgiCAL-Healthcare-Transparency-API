"""upsert_batch, update_batch and replace_rows against a real Postgres."""

import pytest

from etl.common.db import execute_query, replace_rows, update_batch, upsert_batch

TABLE = "etl_test_people"


@pytest.fixture
def table(db):
    with db.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {TABLE}")
        cur.execute(
            f"""
            CREATE TABLE {TABLE} (
              id     TEXT PRIMARY KEY,
              name   TEXT NOT NULL,
              school TEXT,
              year   INTEGER
            )
            """
        )
    yield TABLE
    with db.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {TABLE}")


def rows(table: str) -> dict[str, dict]:
    return {r["id"]: r for r in execute_query(f"SELECT * FROM {table}")}


def test_upsert_accepts_records_with_different_keys(table):
    upsert_batch(table, [{"id": "1", "name": "Ada", "year": 1990}, {"id": "2", "name": "Bo", "school": "UCSF"}], ["id"])
    assert rows(table) == {
        "1": {"id": "1", "name": "Ada", "school": None, "year": 1990},
        "2": {"id": "2", "name": "Bo", "school": "UCSF", "year": None},
    }


def test_upsert_collapses_duplicate_keys_in_one_batch(table):
    # Without de-duplication Postgres raises "ON CONFLICT DO UPDATE command cannot affect row a second time".
    count = upsert_batch(table, [{"id": "1", "name": "first"}, {"id": "1", "name": "second"}], ["id"])
    assert count == 1
    assert rows(table)["1"]["name"] == "second"


def test_upsert_keeps_earlier_batches_when_a_later_one_fails(table):
    records = [{"id": "1", "name": "ok"}, {"id": "2", "name": "ok"}, {"id": "3", "name": None}]
    with pytest.raises(Exception, match="null value"):
        upsert_batch(table, records, ["id"], batch_size=2)
    assert set(rows(table)) == {"1", "2"}


def test_upsert_updates_only_the_named_columns(table):
    upsert_batch(table, [{"id": "1", "name": "Ada", "school": "UCLA"}], ["id"])
    upsert_batch(table, [{"id": "1", "name": "Changed", "school": "Other"}], ["id"], update_columns=["name"])
    assert rows(table)["1"] == {"id": "1", "name": "Changed", "school": "UCLA", "year": None}


def test_update_batch_never_inserts(table):
    upsert_batch(table, [{"id": "1", "name": "Ada"}], ["id"])
    # An upsert of these would fail: name is NOT NULL and the records don't carry it.
    updated = update_batch(table, [{"id": "1", "school": "UCSF", "year": 2001}, {"id": "9", "school": "USC"}], ["id"])
    assert updated == 1
    assert rows(table) == {"1": {"id": "1", "name": "Ada", "school": "UCSF", "year": 2001}}


def test_update_batch_keeps_values_a_record_lacks(table):
    upsert_batch(table, [{"id": "1", "name": "Ada", "school": "UCSF", "year": 2001}], ["id"])
    update_batch(table, [{"id": "1", "year": 2005}, {"id": "1", "school": None, "year": 2005}], ["id"])
    assert rows(table)["1"] == {"id": "1", "name": "Ada", "school": "UCSF", "year": 2005}


def test_replace_rows_swaps_one_owners_rows(table):
    upsert_batch(table, [{"id": "a1", "name": "A"}, {"id": "a2", "name": "A"}, {"id": "b1", "name": "B"}], ["id"])
    replace_rows(table, [{"id": "a3", "name": "A"}], "name = %s", ("A",))
    assert set(rows(table)) == {"a3", "b1"}


def test_replace_rows_rolls_back_on_failure(table):
    upsert_batch(table, [{"id": "a1", "name": "A"}], ["id"])
    with pytest.raises(Exception, match="null value"):
        replace_rows(table, [{"id": "a2", "name": None}], "name = %s", ("A",))
    assert set(rows(table)) == {"a1"}
