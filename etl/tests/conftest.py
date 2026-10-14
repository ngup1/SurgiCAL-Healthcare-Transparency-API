"""
Shared fixtures for the ETL tests.

Extractor tests run on saved copies of real API responses (tests/fixtures/), so
they need no network. Database tests use the compose database (make db-up) and
create their own scratch tables; without a database they are skipped locally and
fail in CI.
"""

import json
import os
from pathlib import Path

import pytest

# Before any etl import: the settings are read once, at import time.
os.environ.setdefault(
    "DATABASE_URL",
    os.getenv("ETL_TEST_DATABASE_URL", "postgresql://surgical:surgical@localhost:5433/surgical?sslmode=disable"),
)

from etl.common.places import PlaceIndex  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def places() -> PlaceIndex:
    """A two-place gazetteer: Hayward and its ZIP code."""
    return PlaceIndex([
        {"place_type": "city", "name": "Hayward", "lat": 37.63, "lng": -122.10},
        {"place_type": "city", "name": "McKinleyville", "lat": 40.95, "lng": -124.10},
        {"place_type": "zip", "name": "94545", "lat": 37.634, "lng": -122.118},
        {"place_type": "zip", "name": "92868", "lat": 33.787, "lng": -117.875},
    ])


@pytest.fixture
def db():
    """A connection to the test database, for setting up and inspecting scratch tables."""
    from etl.common.db import get_db_connection

    try:
        conn = get_db_connection()
    except Exception as exc:
        if os.getenv("CI"):
            raise
        pytest.skip(f"no test database (start it with `make db-up`): {exc}")
    conn.autocommit = True
    yield conn
    conn.close()
