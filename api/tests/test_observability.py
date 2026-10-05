"""Phase 6: request IDs, structured logs, database-outage 503s, and the catch-all 500."""

import json
import logging
import time

import pytest
from psycopg_pool import AsyncConnectionPool

from src.main import app
from src.observability import JsonFormatter, request_id_var
from src.pagination import pagination

DATA_ENDPOINTS = [
    "/hospitals",
    "/hospitals/050801",
    "/providers/1572628497",
    "/prices?cpt=27447",
    "/devices",
    "/search?q=knee",
    "/places?q=san",
]


# --- request IDs ---


async def test_every_response_has_a_request_id(client):
    response = await client.get("/hospitals/050801")
    assert len(response.headers["X-Request-ID"]) == 32


async def test_callers_request_id_is_echoed(client):
    response = await client.get("/health", headers={"X-Request-ID": "demo-123"})
    assert response.headers["X-Request-ID"] == "demo-123"


async def test_unsafe_request_id_is_replaced(client):
    response = await client.get("/health", headers={"X-Request-ID": "bad id\nwith newline"})
    assert response.headers["X-Request-ID"] != "bad id\nwith newline"
    assert len(response.headers["X-Request-ID"]) == 32


async def test_request_id_also_on_errors(client):
    for path in ("/hospitals/999999", "/providers/123"):
        assert "X-Request-ID" in (await client.get(path)).headers


# --- structured logs ---


def test_json_formatter_includes_request_id_and_fields():
    token = request_id_var.set("abc123")
    try:
        record = logging.LogRecord("surgical.access", logging.INFO, __file__, 1, "GET /x 200", None, None)
        record.status, record.duration_ms = 200, 1.5
        line = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)
    assert line["request_id"] == "abc123"
    assert line["message"] == "GET /x 200"
    assert (line["level"], line["status"], line["duration_ms"]) == ("INFO", 200, 1.5)


async def test_access_log_line_per_request(client, caplog):
    with caplog.at_level(logging.INFO, logger="surgical.access"):
        response = await client.get("/hospitals", params={"limit": 2})
    record = next(r for r in caplog.records if r.name == "surgical.access")
    assert (record.method, record.path, record.status) == ("GET", "/hospitals", 200)
    assert record.query == "limit=2"
    assert record.duration_ms >= 0
    assert response.headers["X-Request-ID"]


async def test_health_checks_are_not_logged_at_info(client, caplog):
    with caplog.at_level(logging.INFO, logger="surgical.access"):
        await client.get("/health")
    assert not [r for r in caplog.records if r.name == "surgical.access"]


# --- database unavailable ---


@pytest.fixture
async def database_down(client):
    """Swap in a pool pointed at a port with nothing listening."""
    real_pool = app.state.pool
    dead_pool = AsyncConnectionPool(
        "host=127.0.0.1 port=1 dbname=x user=x connect_timeout=1", min_size=0, max_size=2, timeout=0.5, open=False
    )
    await dead_pool.open(wait=False)
    app.state.pool = dead_pool
    try:
        yield
    finally:
        app.state.pool = real_pool
        await dead_pool.close()


@pytest.mark.parametrize("path", DATA_ENDPOINTS)
async def test_data_endpoints_return_503_when_database_is_down(client, database_down, path):
    start = time.perf_counter()
    response = await client.get(path)
    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}
    assert response.headers["Retry-After"] == "5"
    assert time.perf_counter() - start < 5  # fails fast instead of hanging


async def test_liveness_stays_up_but_readiness_reports_outage(client, database_down):
    assert (await client.get("/health")).status_code == 200
    ready = await client.get("/health/ready")
    assert ready.status_code == 503
    assert ready.json() == {"status": "unavailable", "database": "unreachable"}


# --- unexpected errors ---


async def test_unexpected_error_is_a_generic_500_and_is_logged(client, caplog):
    def broken_dependency():
        raise RuntimeError("secret internal detail")

    app.dependency_overrides[pagination] = broken_dependency
    try:
        with caplog.at_level(logging.ERROR, logger="surgical.error"):
            response = await client.get("/hospitals")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    body = response.json()
    assert body == {"detail": "Internal server error", "request_id": response.headers["X-Request-ID"]}
    assert "secret" not in response.text  # details stay in the logs
    logged = next(r for r in caplog.records if r.name == "surgical.error")
    assert "secret internal detail" in str(logged.exc_info[1])
