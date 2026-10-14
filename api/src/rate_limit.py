"""Per-client rate limiting (token bucket), as pure ASGI middleware.

Each client gets a bucket per tier that holds up to `per_minute` tokens and refills
continuously at per_minute/60 tokens a second; a request spends one token. So a
client can burst up to the full minute's allowance, then continues at the steady
rate. Over the limit, the request gets a 429 with Retry-After and never reaches
the app or the database.

Limits are kept in memory, per worker process: with N workers (and M instances)
a client can get up to N×M times the limit. That is enough to stop one client
from monopolizing a small deployment; a shared store (Redis) would make it exact.

Which address identifies the client matters behind a proxy. By default it is the
ASGI client address, which uvicorn's --proxy-headers takes from the leftmost
X-Forwarded-For entry, and a client can set that entry itself. On a host whose
edge sets a header the client can't forge (e.g. Cloudflare's True-Client-IP),
name it in RATE_LIMIT_CLIENT_IP_HEADER.
"""

import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass

from starlette.types import ASGIApp, Message, Receive, Scope, Send

LIMIT_HEADER = "X-RateLimit-Limit"
REMAINING_HEADER = "X-RateLimit-Remaining"

# Never limited: load-balancer health checks and the interactive docs.
_EXEMPT_PATHS = {"/", "/health", "/health/ready", "/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}
# Idle buckets are dropped once this many clients are tracked, so memory stays bounded.
_MAX_TRACKED = 10_000


@dataclass(frozen=True)
class Tier:
    name: str
    per_minute: int


@dataclass
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    """Token buckets keyed by (client, tier)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._buckets: dict[tuple[str, str], _Bucket] = {}

    def take(self, client: str, tier: Tier) -> tuple[bool, int, int]:
        """Spend a token. Returns (allowed, tokens remaining, seconds until one is available)."""
        now = self._clock()
        rate = tier.per_minute / 60
        bucket = self._buckets.get((client, tier.name))
        if bucket is None:
            if len(self._buckets) >= _MAX_TRACKED:
                self._prune(now)
            bucket = self._buckets[(client, tier.name)] = _Bucket(tier.per_minute, now)
        else:
            bucket.tokens = min(tier.per_minute, bucket.tokens + (now - bucket.updated) * rate)
            bucket.updated = now

        if bucket.tokens >= 1:
            bucket.tokens -= 1
            return True, int(bucket.tokens), 0
        return False, 0, math.ceil((1 - bucket.tokens) / rate)

    def _prune(self, now: float) -> None:
        # A bucket idle for a minute has refilled completely, so forgetting it changes nothing.
        stale = [key for key, b in self._buckets.items() if now - b.updated >= 60]
        for key in stale:
            del self._buckets[key]
        if len(self._buckets) >= _MAX_TRACKED:  # all recently active: drop the oldest half
            for key, _ in sorted(self._buckets.items(), key=lambda kv: kv[1].updated)[: _MAX_TRACKED // 2]:
                del self._buckets[key]


class RateLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        per_minute: int,
        search_per_minute: int,
        client_ip_header: str = "",
        limiter: RateLimiter | None = None,
    ) -> None:
        self.app = app
        self.default = Tier("default", per_minute)
        # /search runs four similarity queries on four pooled connections per call.
        self.search = Tier("search", search_per_minute)
        self.client_ip_header = client_ip_header.lower().encode()
        self.limiter = limiter or RateLimiter()

    def _client(self, scope: Scope) -> str:
        if self.client_ip_header:
            for name, value in scope.get("headers", []):
                if name == self.client_ip_header:
                    return value.decode("latin-1").split(",")[0].strip()
        return (scope.get("client") or ("unknown",))[0]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or scope.get("method") == "OPTIONS" or path in _EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return

        tier = self.search if path == "/search" or path.startswith("/search/") else self.default
        allowed, remaining, retry_after = self.limiter.take(self._client(scope), tier)
        limit_headers = [
            (LIMIT_HEADER.lower().encode(), str(tier.per_minute).encode()),
            (REMAINING_HEADER.lower().encode(), str(remaining).encode()),
        ]

        if not allowed:
            body = json.dumps(
                {"detail": f"Rate limit exceeded: {tier.per_minute} requests per minute. Retry in {retry_after} s."}
            ).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 429,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                        (b"retry-after", str(retry_after).encode()),
                        *limit_headers,
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        async def send_with_limits(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []), *limit_headers]
            await send(message)

        await self.app(scope, receive, send_with_limits)
