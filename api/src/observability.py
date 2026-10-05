"""Structured logging and per-request context.

- Logs are one JSON object per line (LOG_JSON=false for plain text locally), so hosts
  like Render can search and filter them by field.
- Every request gets an ID: the caller's X-Request-ID if it looks safe, else a new one.
  It's returned in the X-Request-ID response header and included in every log line
  written while handling the request, so a user's error can be matched to its logs.
- The middleware writes one access-log line per request, and is the last line of
  defence for unexpected exceptions: it logs the traceback and returns a generic 500.
"""

import json
import logging
import re
import sys
import time
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime

from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-ID"
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
# Load-balancer health checks hit these every few seconds; log them at DEBUG only.
_QUIET_PATHS = {"/health", "/health/ready"}

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
access_log = logging.getLogger("surgical.access")
error_log = logging.getLogger("surgical.error")

# Extra fields copied from `logger.info(..., extra={...})` into the JSON line.
_EXTRA_FIELDS = ("method", "path", "query", "status", "duration_ms", "client")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        payload.update({key: getattr(record, key) for key in _EXTRA_FIELDS if hasattr(record, key)})
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", json_format: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter() if json_format else logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # Route uvicorn's own loggers through the same handler instead of its defaults.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True


def _request_id(scope: Scope) -> str:
    for name, value in scope.get("headers", []):
        if name == b"x-request-id":
            candidate = value.decode("latin-1")
            if _SAFE_REQUEST_ID.match(candidate):
                return candidate
    return uuid.uuid4().hex


class RequestContextMiddleware:
    """Pure ASGI middleware: request ID, access log, and catch-all 500."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _request_id(scope)
        token = request_id_var.set(request_id)
        start = time.perf_counter()
        status = 500
        response_started = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                status = message["status"]
                response_started = True
                message["headers"] = [*message.get("headers", []), (b"x-request-id", request_id.encode())]
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            error_log.exception("Unhandled error")
            if response_started:
                raise
            body = json.dumps({"detail": "Internal server error", "request_id": request_id}).encode()
            await send_with_request_id(
                {
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
                }
            )
            await send({"type": "http.response.body", "body": body})
        finally:
            path = scope.get("path", "")
            access_log.log(
                logging.DEBUG if path in _QUIET_PATHS else logging.INFO,
                "%s %s %s",
                scope.get("method"),
                path,
                status,
                extra={
                    "method": scope.get("method"),
                    "path": path,
                    "query": scope.get("query_string", b"").decode("latin-1"),
                    "status": status,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                    "client": (scope.get("client") or ("-",))[0],
                },
            )
            request_id_var.reset(token)
