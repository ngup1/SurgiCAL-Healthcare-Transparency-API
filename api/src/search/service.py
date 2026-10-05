"""Fuzzy search across procedures, providers, hospitals, and devices.

Matching uses pg_trgm word similarity (`q <% column`), so a short query like "knee"
matches the word inside a long name.
"""

import asyncio

from psycopg_pool import AsyncConnectionPool

from src.database import fetch_all

# group name -> query; each takes (q, q, limit)
QUERIES = {
    "procedures": """
        SELECT code, description, category, is_surgical,
               word_similarity(%s, description) AS relevance
        FROM cpt_codes
        WHERE %s <%% description
        ORDER BY relevance DESC
        LIMIT %s
    """,
    "providers": """
        SELECT p.npi, p.first_name, p.last_name, p.specialty, p.city, p.state,
               word_similarity(%s, p.first_name || ' ' || p.last_name) AS relevance
        FROM providers p
        WHERE %s <%% (p.first_name || ' ' || p.last_name)
        ORDER BY relevance DESC
        LIMIT %s
    """,
    "hospitals": """
        SELECT ccn, name, city, state,
               word_similarity(%s, name) AS relevance
        FROM hospitals
        WHERE %s <%% name
        ORDER BY relevance DESC
        LIMIT %s
    """,
    "devices": """
        SELECT id, brand_name, manufacturer, medical_specialty,
               word_similarity(%s, brand_name) AS relevance
        FROM devices
        WHERE %s <%% brand_name
        ORDER BY relevance DESC
        LIMIT %s
    """,
}


async def search(pool: AsyncConnectionPool, q: str, limit: int) -> dict[str, list[dict]]:
    """Run the four group queries concurrently, each on its own pooled connection."""

    async def run(sql: str) -> list[dict]:
        async with pool.connection() as conn:
            return await fetch_all(conn, sql, (q, q, limit))

    results = await asyncio.gather(*(run(sql) for sql in QUERIES.values()))
    return dict(zip(QUERIES, results, strict=True))
