"""Base model for response schemas (fastapi-best-practices: a shared custom base model)."""

from pydantic import BaseModel, ConfigDict


class CustomModel(BaseModel):
    # Rows come from the database as dicts; NUMERIC values arrive as Decimal and are
    # validated into the float fields below, so JSON shows plain numbers.
    model_config = ConfigDict(populate_by_name=True)
