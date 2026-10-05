from fastapi import APIRouter

from src.config import settings

router = APIRouter()


@router.get("", summary="Service status")
def health_check():
    # GIT_COMMIT is baked into the image by CI, so a deploy can be traced to its commit.
    return {"status": "ok", "commit": settings.git_commit}
