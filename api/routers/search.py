"""Unified search endpoint: fuzzy search across procedures, providers, and devices."""

from fastapi import APIRouter, Depends, Query
from psycopg2.extras import RealDictCursor

from api.dependencies import get_db

router = APIRouter()


@router.get("")
def unified_search(
    q: str = Query(..., min_length=2, max_length=100, description="Search query"),
    limit: int = Query(10, ge=1, le=50),
    conn=Depends(get_db),
):
    """
    Fuzzy search across CPT procedures, providers, hospitals, and devices.

    Returns categorized results with relevance scoring via pg_trgm word similarity,
    so a short query like "knee" matches the word inside a long name.
    """
    results = {
        "procedures": [],
        "providers": [],
        "hospitals": [],
        "devices": [],
    }

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Search CPT codes by description
        cur.execute(
            """
            SELECT code, description, category, is_surgical,
                   word_similarity(%s, description) AS relevance
            FROM cpt_codes
            WHERE %s <%% description
            ORDER BY relevance DESC
            LIMIT %s
            """,
            (q, q, limit),
        )
        results["procedures"] = [dict(r) for r in cur.fetchall()]

        # Search providers by name
        cur.execute(
            """
            SELECT p.npi, p.first_name, p.last_name, p.specialty, p.city, p.state,
                   word_similarity(%s, p.first_name || ' ' || p.last_name) AS relevance
            FROM providers p
            WHERE %s <%% (p.first_name || ' ' || p.last_name)
            ORDER BY relevance DESC
            LIMIT %s
            """,
            (q, q, limit),
        )
        results["providers"] = [dict(r) for r in cur.fetchall()]

        # Search hospitals by name
        cur.execute(
            """
            SELECT ccn, name, city, state,
                   word_similarity(%s, name) AS relevance
            FROM hospitals
            WHERE %s <%% name
            ORDER BY relevance DESC
            LIMIT %s
            """,
            (q, q, limit),
        )
        results["hospitals"] = [dict(r) for r in cur.fetchall()]

        # Search devices by brand name
        cur.execute(
            """
            SELECT id, brand_name, manufacturer, medical_specialty,
                   word_similarity(%s, brand_name) AS relevance
            FROM devices
            WHERE %s <%% brand_name
            ORDER BY relevance DESC
            LIMIT %s
            """,
            (q, q, limit),
        )
        results["devices"] = [dict(r) for r in cur.fetchall()]

    return results
