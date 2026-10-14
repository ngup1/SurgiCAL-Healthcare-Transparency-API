"""HTTP utilities for fetching data from APIs and file downloads."""

from __future__ import annotations

import time
import zlib
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from etl.common.config import get_logger, get_settings

logger = get_logger(__name__)

# The "Mozilla/5.0 (compatible; ...)" form is the convention for declared bots; hospital
# websites often refuse clients whose User-Agent has no such prefix.
USER_AGENT = "Mozilla/5.0 (compatible; SurgiCAL-ETL/0.2; healthcare price transparency research)"

# Worth another attempt: rate limiting and server-side failures. Any other 4xx
# (bad request, not found) will fail the same way every time.
RETRY_STATUSES = {429, 500, 502, 503, 504}


class FileTooLarge(Exception):
    """A download was stopped because it passed the size cap."""


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRY_STATUSES
    # Timeouts, dropped connections, DNS failures.
    return isinstance(exc, httpx.TransportError)


_retry = retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=5, max=120),
    reraise=True,
)


class RateLimitedClient:
    """
    HTTP client with rate limiting and automatic retries.

    The delay is tracked per client, so a step makes one client and uses it for
    every request; a new client per request would never wait.
    """

    def __init__(self, delay_ms: int | None = None, timeout: float = 60.0):
        settings = get_settings()
        self.delay_ms = settings.request_delay_ms if delay_ms is None else delay_ms
        self._last_request_time = 0.0
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )

    def _wait(self):
        elapsed_ms = (time.monotonic() - self._last_request_time) * 1000
        if elapsed_ms < self.delay_ms:
            time.sleep((self.delay_ms - elapsed_ms) / 1000)
        self._last_request_time = time.monotonic()

    @_retry
    def get(self, url: str, **kwargs) -> httpx.Response:
        self._wait()
        logger.debug(f"GET {url}")
        response = self._client.get(url, **kwargs)
        response.raise_for_status()
        return response

    @_retry
    def post(self, url: str, **kwargs) -> httpx.Response:
        self._wait()
        logger.debug(f"POST {url}")
        response = self._client.post(url, **kwargs)
        response.raise_for_status()
        return response

    def get_once(self, url: str, **kwargs) -> httpx.Response:
        """GET with no retries, for probing URLs that may well not exist."""
        self._wait()
        response = self._client.get(url, **kwargs)
        response.raise_for_status()
        return response

    def get_json(self, url: str, params: dict | None = None, *, missing_ok: bool = False) -> Any:
        """
        GET and decode JSON. With missing_ok, a 404 returns None instead of raising:
        openFDA answers 404 when a search simply has no results.
        """
        try:
            return self.get(url, params=params).json()
        except httpx.HTTPStatusError as exc:
            if missing_ok and exc.response.status_code == 404:
                return None
            raise

    def post_json(self, url: str, body: dict) -> Any:
        return self.post(url, json=body).json()

    @_retry
    def download(self, url: str, dest: Path, max_bytes: int | None = None) -> int:
        """
        Stream a URL to a file and return the bytes written.

        Raises FileTooLarge (and removes the partial file) once max_bytes is passed.
        """
        self._wait()
        dest.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        try:
            with self._client.stream("GET", url) as response:
                response.raise_for_status()
                declared = int(response.headers.get("content-length") or 0)
                if max_bytes and declared > max_bytes:
                    raise FileTooLarge(f"{url} is {declared / 1e6:.0f} MB (cap {max_bytes / 1e6:.0f} MB)")
                with open(dest, "wb") as fh:
                    for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                        total += len(chunk)
                        if max_bytes and total > max_bytes:
                            raise FileTooLarge(f"{url} passed the {max_bytes / 1e6:.0f} MB cap")
                        fh.write(chunk)
        except BaseException:
            dest.unlink(missing_ok=True)
            raise
        logger.info(f"Downloaded {total / 1e6:.1f} MB from {url[:100]}")
        return total

    def peek(self, url: str, max_bytes: int = 65536) -> bytes:
        """The first max_bytes of a URL's body (decoded if gzip-compressed), without downloading the rest."""
        self._wait()
        chunks: list[bytes] = []
        total = 0
        with self._client.stream("GET", url, headers={"Range": f"bytes=0-{max_bytes - 1}"}) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes(chunk_size=16384):
                chunks.append(chunk)
                total += len(chunk)
                if total >= max_bytes:
                    break
        data = b"".join(chunks)[:max_bytes]
        if data[:2] == b"\x1f\x8b":  # a .gz file: inflate as much as the prefix holds
            data = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(data, max_bytes)
        return data

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def openfda_params(params: dict) -> dict:
    """Add the openFDA API key, when one is configured, to a request's query parameters."""
    key = get_settings().openfda_api_key
    return {**params, "api_key": key} if key else params
