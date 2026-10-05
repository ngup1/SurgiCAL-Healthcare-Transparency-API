"""Unified search endpoint."""

from fastapi import APIRouter, Depends, Query

from src.database import get_db
from src.docs import SEARCH_EXAMPLES
from src.search import service

router = APIRouter()


@router.get("", summary="Search everything")
def unified_search(
    q: str = Query(..., min_length=2, max_length=100, description="Search text", openapi_examples=SEARCH_EXAMPLES),
    limit: int = Query(10, ge=1, le=50, description="Maximum results per group"),
    conn=Depends(get_db),
):
    """
    Fuzzy search across CPT procedures, providers, hospitals, and devices.

    Returns categorized results with relevance scoring via pg_trgm word similarity,
    so a short query like "knee" matches the word inside a long name.
    """
    return service.search(conn, q, limit)
