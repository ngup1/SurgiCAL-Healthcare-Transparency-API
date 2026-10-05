from fastapi import APIRouter

from src.config import settings
from src.health.schemas import HealthStatus

router = APIRouter()


@router.get("", summary="Service status", response_model=HealthStatus)
async def health_check():
    # GIT_COMMIT is baked into the image by CI, so a deploy can be traced to its commit.
    return {"status": "ok", "commit": settings.git_commit}
