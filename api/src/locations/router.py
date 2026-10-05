"""Place lookup, so callers can find valid city / county / ZIP values."""

from fastapi import APIRouter, Depends, Query
from psycopg import AsyncConnection

from src.database import get_db
from src.docs import PLACE_EXAMPLES
from src.locations import service
from src.locations.constants import PlaceType
from src.locations.schemas import Place

router = APIRouter()


@router.get("", summary="Find California places", response_model=list[Place])
async def search_places(
    q: str = Query(..., min_length=2, max_length=80, description="Start of a name", openapi_examples=PLACE_EXAMPLES),
    type: PlaceType | None = Query(None, description="Only this kind of place"),
    limit: int = Query(10, ge=1, le=50, description="Maximum results"),
    conn: AsyncConnection = Depends(get_db),
):
    """
    California cities, counties, and ZIP codes matching `q`, for use as the `city`, `county`,
    or `zip` filter on hospitals, providers, and prices. Prefix matches come first.
    """
    return await service.search_places(conn, q.strip(), type, limit)
