"""Unified search endpoint."""

from fastapi import APIRouter, Depends, Query
from psycopg_pool import AsyncConnectionPool

from src.database import get_pool
from src.docs import SEARCH_EXAMPLES
from src.search import service

router = APIRouter()


@router.get("", summary="Search everything")
async def unified_search(
    q: str = Query(..., min_length=2, max_length=100, description="Search text", openapi_examples=SEARCH_EXAMPLES),
    limit: int = Query(10, ge=1, le=50, description="Maximum results per group"),
    pool: AsyncConnectionPool = Depends(get_pool),
):
    """
    Fuzzy search across CPT procedures, providers, hospitals, and devices.

    Returns categorized results with relevance scoring via pg_trgm word similarity,
    so a short query like "knee" matches the word inside a long name.
    """
    return await service.search(pool, q, limit)
