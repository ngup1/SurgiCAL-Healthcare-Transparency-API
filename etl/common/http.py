"""HTTP utilities for fetching data from APIs and CSV downloads."""

from __future__ import annotations

import time
from io import StringIO
from typing import Any

import httpx
import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

from etl.common.config import get_settings, get_logger

logger = get_logger(__name__)


class RateLimitedClient:
    """HTTP client with rate limiting and automatic retries."""

    def __init__(self, delay_ms: int | None = None):
        settings = get_settings()
        self.delay_ms = delay_ms or settings.request_delay_ms
        self._last_request_time = 0.0
        self._client = httpx.Client(
            timeout=60.0,
            follow_redirects=True,
            headers={
                "User-Agent": "SurgiCAL-ETL/0.1 (Healthcare price transparency research)"
            },
        )

    def _wait(self):
        elapsed_ms = (time.time() - self._last_request_time) * 1000
        if elapsed_ms < self.delay_ms:
            time.sleep((self.delay_ms - elapsed_ms) / 1000)
        self._last_request_time = time.time()

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=2, min=5, max=120))
    def get(self, url: str, **kwargs) -> httpx.Response:
        self._wait()
        logger.debug(f"GET {url}")
        response = self._client.get(url, **kwargs)
        response.raise_for_status()
        return response

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=2, min=5, max=120))
    def post(self, url: str, **kwargs) -> httpx.Response:
        self._wait()
        logger.debug(f"POST {url}")
        response = self._client.post(url, **kwargs)
        response.raise_for_status()
        return response

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def fetch_json(url: str, params: dict | None = None) -> Any:
    """Fetch JSON from a URL."""
    with RateLimitedClient() as client:
        response = client.get(url, params=params)
        return response.json()


def post_json(url: str, body: dict) -> Any:
    """POST a JSON body to a URL and return the response JSON."""
    with RateLimitedClient() as client:
        response = client.post(url, json=body)
        return response.json()


def fetch_csv(url: str) -> pd.DataFrame:
    """Fetch a CSV from a URL and return as a DataFrame."""
    with RateLimitedClient() as client:
        response = client.get(url)
        return pd.read_csv(StringIO(response.text))
