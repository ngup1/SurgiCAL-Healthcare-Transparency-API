"""Rate limiting: the token bucket, and the middleware's 429s, headers, tiers and exemptions."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.rate_limit import RateLimiter, RateLimitMiddleware, Tier


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_bucket_allows_a_burst_then_refills():
    clock = FakeClock()
    limiter = RateLimiter(clock)
    tier = Tier("default", 60)  # one token a second

    results = [limiter.take("1.2.3.4", tier) for _ in range(60)]
    assert all(allowed for allowed, _, _ in results)
    assert results[-1][1] == 0

    assert limiter.take("1.2.3.4", tier) == (False, 0, 1)
    clock.now += 1
    assert limiter.take("1.2.3.4", tier)[0] is True
    assert limiter.take("1.2.3.4", tier)[0] is False


def test_clients_and_tiers_have_separate_buckets():
    limiter = RateLimiter(FakeClock())
    default, search = Tier("default", 1), Tier("search", 1)
    assert limiter.take("a", default)[0]
    assert not limiter.take("a", default)[0]
    assert limiter.take("b", default)[0]
    assert limiter.take("a", search)[0]


def test_retry_after_reflects_the_refill_rate():
    limiter = RateLimiter(FakeClock())
    tier = Tier("search", 6)  # one token every 10 s
    for _ in range(6):
        limiter.take("a", tier)
    assert limiter.take("a", tier) == (False, 0, 10)


def make_app(**options) -> FastAPI:
    app = FastAPI()

    @app.get("/hospitals")
    async def hospitals():
        return []

    @app.get("/search")
    async def search():
        return {}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    app.add_middleware(RateLimitMiddleware, **{"per_minute": 3, "search_per_minute": 1, **options})
    return app


@pytest.fixture
async def limited_client():
    async with AsyncClient(transport=ASGITransport(app=make_app()), base_url="http://test") as ac:
        yield ac


async def test_over_the_limit_returns_429_with_retry_after(limited_client):
    for remaining in (2, 1, 0):
        response = await limited_client.get("/hospitals")
        assert response.status_code == 200
        assert response.headers["X-RateLimit-Limit"] == "3"
        assert response.headers["X-RateLimit-Remaining"] == str(remaining)

    response = await limited_client.get("/hospitals")
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) >= 1
    assert "3 requests per minute" in response.json()["detail"]


async def test_search_has_its_own_stricter_limit(limited_client):
    assert (await limited_client.get("/search")).status_code == 200
    assert (await limited_client.get("/search")).status_code == 429
    assert (await limited_client.get("/hospitals")).status_code == 200


async def test_health_checks_are_never_limited(limited_client):
    for _ in range(10):
        assert (await limited_client.get("/health")).status_code == 200


async def test_client_ip_header_identifies_clients():
    app = make_app(client_ip_header="True-Client-IP")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for _ in range(3):
            await ac.get("/hospitals", headers={"True-Client-IP": "203.0.113.1"})
        assert (await ac.get("/hospitals", headers={"True-Client-IP": "203.0.113.1"})).status_code == 429
        # Another client behind the same proxy connection has its own allowance.
        assert (await ac.get("/hospitals", headers={"True-Client-IP": "203.0.113.2"})).status_code == 200
