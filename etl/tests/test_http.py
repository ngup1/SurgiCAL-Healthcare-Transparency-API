"""The HTTP layer: what is retried, and openFDA's 404-means-empty."""

import httpx
import pytest
from tenacity import wait_none

from etl.common.http import FileTooLarge, RateLimitedClient
from etl.tests.conftest import load_fixture


@pytest.fixture
def client_for(monkeypatch):
    """Build a client whose requests go to a handler function; retries don't sleep."""
    for method in ("get", "post", "download"):
        monkeypatch.setattr(getattr(RateLimitedClient, method).retry, "wait", wait_none())

    def build(handler) -> tuple[RateLimitedClient, list[httpx.Request]]:
        calls: list[httpx.Request] = []

        def record(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return handler(request, len(calls))

        client = RateLimitedClient(delay_ms=0)
        client._client = httpx.Client(transport=httpx.MockTransport(record))
        return client, calls

    return build


def test_404_is_not_retried(client_for):
    client, calls = client_for(lambda request, n: httpx.Response(404, json=load_fixture("fda_404.json")))
    with pytest.raises(httpx.HTTPStatusError):
        client.get("https://api.fda.gov/device/pma.json")
    assert len(calls) == 1


def test_openfda_404_is_an_empty_result(client_for):
    client, calls = client_for(lambda request, n: httpx.Response(404, json=load_fixture("fda_404.json")))
    assert client.get_json("https://api.fda.gov/device/pma.json", missing_ok=True) is None
    assert len(calls) == 1


@pytest.mark.parametrize("status", [429, 500, 503])
def test_rate_limit_and_server_errors_are_retried(client_for, status):
    client, calls = client_for(
        lambda request, n: httpx.Response(status) if n < 3 else httpx.Response(200, json={"ok": True})
    )
    assert client.get_json("https://example.org/data") == {"ok": True}
    assert len(calls) == 3


def test_timeouts_are_retried_then_raised(client_for):
    def handler(request, n):
        raise httpx.ReadTimeout("slow", request=request)

    client, calls = client_for(handler)
    with pytest.raises(httpx.ReadTimeout):
        client.get("https://example.org/data")
    assert len(calls) == 5


def test_download_stops_at_the_size_cap(client_for, tmp_path):
    client, _ = client_for(lambda request, n: httpx.Response(200, content=b"x" * 5000))
    dest = tmp_path / "file.bin"
    with pytest.raises(FileTooLarge):
        client.download("https://example.org/big", dest, max_bytes=1000)
    assert not dest.exists()

    assert client.download("https://example.org/big", dest, max_bytes=10_000) == 5000
    assert dest.read_bytes() == b"x" * 5000


def test_one_client_spaces_its_requests(client_for, monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr("etl.common.http.time.sleep", slept.append)
    client, _ = client_for(lambda request, n: httpx.Response(200, json={}))
    client.delay_ms = 300
    client.get("https://example.org/a")
    client.get("https://example.org/b")
    assert len(slept) == 1 and 0.2 < slept[0] <= 0.3
