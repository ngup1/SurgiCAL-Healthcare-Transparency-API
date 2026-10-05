from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from psycopg import OperationalError
from psycopg_pool import AsyncConnectionPool

from src.config import settings
from src.database import get_pool
from src.health.schemas import HealthStatus, ReadinessStatus

router = APIRouter()

READY_TIMEOUT_SECONDS = 3


@router.get("", summary="Service status", response_model=HealthStatus)
async def health_check():
    # Liveness: answers without touching the database, so a database outage doesn't
    # make the host restart an API process that is itself fine.
    # GIT_COMMIT is baked into the image by CI, so a deploy can be traced to its commit.
    return {"status": "ok", "commit": settings.git_commit}


@router.get(
    "/ready",
    summary="Readiness: can the API reach its database?",
    response_model=ReadinessStatus,
    responses={503: {"model": ReadinessStatus, "description": "The database is unreachable"}},
)
async def readiness_check(pool: AsyncConnectionPool = Depends(get_pool)):
    """Runs `SELECT 1` on a pooled connection. Used by the Docker health check and CI."""
    try:
        async with pool.connection(timeout=READY_TIMEOUT_SECONDS) as conn:
            await conn.execute("SELECT 1")
    except OperationalError:
        return JSONResponse(status_code=503, content={"status": "unavailable", "database": "unreachable"})
    return {"status": "ok", "database": "ok"}
