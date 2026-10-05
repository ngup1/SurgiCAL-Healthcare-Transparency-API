"""Shared paging: `limit`/`offset` parameters and an X-Total-Count header."""

from dataclasses import dataclass
from typing import Any

from fastapi import Query, Response
from psycopg import AsyncConnection

from src.database import fetch_all, fetch_one

TOTAL_COUNT_HEADER = "X-Total-Count"

# Documents the header on list endpoints (merged into each route's 200 response).
PAGINATED_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "headers": {
            TOTAL_COUNT_HEADER: {
                "description": "Total matching rows across all pages",
                "schema": {"type": "integer"},
            }
        }
    }
}


@dataclass(frozen=True)
class Pagination:
    limit: int
    offset: int


def pagination(
    limit: int = Query(50, ge=1, le=200, description="Maximum rows to return"),
    offset: int = Query(0, ge=0, description="Rows to skip, for paging"),
) -> Pagination:
    return Pagination(limit=limit, offset=offset)


async def paginate(
    conn: AsyncConnection,
    base_sql: str,
    params: list,
    *,
    order_by: str,
    page: Pagination,
    order_params: list | None = None,
) -> tuple[list[dict], int]:
    """Run `base_sql` (no ORDER BY/LIMIT) for one page of rows plus the total match count."""
    total = await fetch_one(conn, f"SELECT count(*) AS n FROM ({base_sql}) AS matches", params)
    rows = await fetch_all(
        conn,
        f"{base_sql} ORDER BY {order_by} LIMIT %s OFFSET %s",
        [*params, *(order_params or []), page.limit, page.offset],
    )
    return rows, total["n"]


def set_total_count(response: Response, total: int) -> None:
    response.headers[TOTAL_COUNT_HEADER] = str(total)
