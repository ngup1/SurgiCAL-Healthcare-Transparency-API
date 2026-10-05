from fastapi import Depends, Path
from psycopg import AsyncConnection

from src.constants import NPI_PATTERN
from src.database import get_db
from src.docs import NPI_EXAMPLES
from src.providers import service
from src.providers.exceptions import ProviderNotFound


async def valid_provider_npi(
    npi: str = Path(
        ..., pattern=NPI_PATTERN, description="National Provider Identifier", openapi_examples=NPI_EXAMPLES
    ),
    conn: AsyncConnection = Depends(get_db),
) -> dict:
    """The provider for `npi`, or 404."""
    provider = await service.get_provider(conn, npi)
    if provider is None:
        raise ProviderNotFound()
    return provider
