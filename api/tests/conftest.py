"""
Shared test setup.

Tests run against the docker-compose database seeded with the mock data
(`make db-up`). Expected values are read from the same fixtures the seed was
generated from (api/seed/data/*.json), so regenerating the data keeps the
tests valid.
"""

import json
import os
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

# Point the app at the compose database before it is imported. DB_* values already in
# the environment win, so CI can target its own database. DATABASE_URL is cleared (it
# would take precedence, and api/.env may point at a hosted database) unless
# TEST_DATABASE_URL is set explicitly.
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "")
for key, value in {
    "DB_HOST": "localhost",
    "DB_PORT": "5433",
    "DB_NAME": "surgical",
    "DB_USER": "surgical",
    "DB_PASSWORD": "surgical",
    "DB_SSL_MODE": "disable",
}.items():
    os.environ.setdefault(key, value)

import psycopg  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from src.config import settings  # noqa: E402
from src.main import app  # noqa: E402

SEED_DATA_DIR = Path(__file__).resolve().parents[1] / "seed" / "data"

# Stable mock-data identifiers (see DEPLOYMENT_PLAN.md §6).
BAYSHORE_CCN = "050801"  # large SF hospital, has quality data
NO_QUALITY_CCN = "051382"  # High Sierra, no hospital_quality row
UNKNOWN_CCN = "999999"
PROVIDER_NPI = "1572628497"  # Daniel Castellanos, orthopaedic surgery
UNKNOWN_NPI = "1000000000"
KNEE_CPT = "27447"
NO_DEVICE_CPT = "15823"  # surgical code with no mapped devices
PACEMAKER_ID = "68670191-dbe2-5dbd-a693-5f7118cab2c2"
UNKNOWN_DEVICE_ID = "00000000-0000-0000-0000-000000000000"
DOWNTOWN_LA = {"lat": 34.0522, "lng": -118.2437}


@pytest.fixture(scope="session", autouse=True)
def require_database() -> None:
    try:
        psycopg.connect(settings.db_conninfo, connect_timeout=5).close()
    except psycopg.OperationalError as exc:
        pytest.exit(
            f"Test database is not reachable ({exc}). Start it with `make db-up`.",
            returncode=2,
        )


@pytest.fixture(scope="session")
def seed() -> dict[str, list[dict]]:
    """Mock-data rows by table name, e.g. seed["hospitals"]."""
    return {p.stem: json.loads(p.read_text()) for p in SEED_DATA_DIR.glob("*.json")}


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    # ASGITransport doesn't send lifespan events, so run the app's lifespan here:
    # it opens the database pool the routes borrow connections from.
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
