"""SurgiCAL FastAPI application entry point."""

import os

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from api.exceptions import validation_exception_handler
from api.routers import devices, hospitals, prices, providers, search

app = FastAPI(
    title="SurgiCAL API",
    description="Healthcare marketplace API for surgical pricing, quality, and device data",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.add_exception_handler(RequestValidationError, validation_exception_handler)

app.include_router(hospitals.router, prefix="/hospitals", tags=["hospitals"])
app.include_router(providers.router, prefix="/providers", tags=["providers"])
app.include_router(prices.router, prefix="/prices", tags=["prices"])
app.include_router(devices.router, prefix="/devices", tags=["devices"])
app.include_router(search.router, prefix="/search", tags=["search"])


@app.get("/health")
def health_check():
    # GIT_COMMIT is baked into the image by CI, so a deploy can be traced to its commit.
    return {"status": "ok", "commit": os.getenv("GIT_COMMIT", "unknown")}
