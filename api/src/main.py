"""SurgiCAL FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from src.config import settings
from src.devices.router import router as devices_router
from src.docs import API_DESCRIPTION, TAGS_METADATA, VALIDATION_RESPONSES
from src.exceptions import NotFound, not_found_handler, validation_exception_handler
from src.health.router import router as health_router
from src.hospitals.router import router as hospitals_router
from src.prices.router import router as prices_router
from src.providers.router import router as providers_router
from src.search.router import router as search_router

app = FastAPI(
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
)

app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(NotFound, not_found_handler)

for router, prefix in [
    (search_router, "/search"),
    (prices_router, "/prices"),
    (hospitals_router, "/hospitals"),
    (providers_router, "/providers"),
    (devices_router, "/devices"),
]:
    app.include_router(router, prefix=prefix, tags=[prefix.strip("/")], responses=VALIDATION_RESPONSES)
app.include_router(health_router, prefix="/health", tags=["health"])


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/docs") if settings.show_docs else {"name": app.title, "health": "/health"}
