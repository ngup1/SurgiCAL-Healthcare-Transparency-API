"""SurgiCAL FastAPI application entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from psycopg import OperationalError

from src.config import settings
from src.database import create_pool
from src.devices.router import router as devices_router
from src.docs import API_DESCRIPTION, TAGS_METADATA, VALIDATION_RESPONSES
from src.exceptions import (
    NotFound,
    RequestError,
    database_unavailable_handler,
    not_found_handler,
    request_error_handler,
    validation_exception_handler,
)
from src.health.router import router as health_router
from src.hospitals.router import router as hospitals_router
from src.locations.router import router as places_router
from src.observability import REQUEST_ID_HEADER, RequestContextMiddleware, configure_logging
from src.pagination import TOTAL_COUNT_HEADER
from src.prices.router import router as prices_router
from src.providers.router import router as providers_router
from src.search.router import router as search_router

configure_logging(settings.log_level, settings.log_json)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the connection pool at startup and close it at shutdown."""
    pool = create_pool()
    # wait=False: the app starts (and /health answers) even if the database is
    # briefly unreachable; requests wait for a connection instead.
    await pool.open(wait=False)
    app.state.pool = pool
    try:
        yield
    finally:
        await pool.close()


app = FastAPI(
    lifespan=lifespan,
    title="SurgiCAL API",
    summary="Healthcare transparency API",
    description=API_DESCRIPTION,
    version="0.2.0",
    openapi_tags=TAGS_METADATA,
    # fastapi-best-practices: docs can be hidden (SHOW_DOCS=false); on for the public demo.
    openapi_url="/openapi.json" if settings.show_docs else None,
    swagger_ui_parameters={
        "tryItOutEnabled": True,  # every endpoint opens ready to Execute
        "displayRequestDuration": True,
        "defaultModelsExpandDepth": -1,  # hide the schema list at the bottom
    },
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
    expose_headers=[TOTAL_COUNT_HEADER, REQUEST_ID_HEADER],  # readable by browser clients
)
# Added last, so it's outermost: it sees every request and response, including CORS.
app.add_middleware(RequestContextMiddleware)

app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(NotFound, not_found_handler)
app.add_exception_handler(RequestError, request_error_handler)
app.add_exception_handler(OperationalError, database_unavailable_handler)

for router, prefix in [
    (search_router, "/search"),
    (prices_router, "/prices"),
    (hospitals_router, "/hospitals"),
    (providers_router, "/providers"),
    (devices_router, "/devices"),
    (places_router, "/places"),
]:
    app.include_router(router, prefix=prefix, tags=[prefix.strip("/")], responses=VALIDATION_RESPONSES)
app.include_router(health_router, prefix="/health", tags=["health"])


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse("/docs") if settings.show_docs else {"name": app.title, "health": "/health"}
