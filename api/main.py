"""SurgiCAL FastAPI application entry point."""

import os

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from api.docs import API_DESCRIPTION, TAGS_METADATA, VALIDATION_RESPONSES
from api.exceptions import validation_exception_handler
from api.routers import devices, hospitals, prices, providers, search

# Docs are on unless SHOW_DOCS=false (fastapi-best-practices: be able to hide them).
SHOW_DOCS = os.getenv("SHOW_DOCS", "true").lower() != "false"

app = FastAPI(
    title="SurgiCAL API",
    summary="Healthcare transparency API",
    description=API_DESCRIPTION,
    version="0.2.0",
    openapi_tags=TAGS_METADATA,
    openapi_url="/openapi.json" if SHOW_DOCS else None,
    swagger_ui_parameters={
        "tryItOutEnabled": True,  # every endpoint opens ready to Execute
        "displayRequestDuration": True,
        "defaultModelsExpandDepth": -1,  # hide the schema list at the bottom
    },
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.add_exception_handler(RequestValidationError, validation_exception_handler)

for router, prefix in [
    (search.router, "/search"),
    (prices.router, "/prices"),
    (hospitals.router, "/hospitals"),
    (providers.router, "/providers"),
    (devices.router, "/devices"),
]:
    app.include_router(router, prefix=prefix, tags=[prefix.strip("/")], responses=VALIDATION_RESPONSES)


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/docs") if SHOW_DOCS else {"name": app.title, "health": "/health"}


@app.get("/health", tags=["health"], summary="Service status")
def health_check():
    # GIT_COMMIT is baked into the image by CI, so a deploy can be traced to its commit.
    return {"status": "ok", "commit": os.getenv("GIT_COMMIT", "unknown")}
