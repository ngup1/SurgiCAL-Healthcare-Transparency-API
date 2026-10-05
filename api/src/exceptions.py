"""Error types, response models, and the handlers that give every error the same shape."""

from typing import Any

from fastapi import Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.constants import PATTERN_MESSAGES


class FieldError(BaseModel):
    field: str
    location: str
    message: str
    input: Any = None


class ValidationErrorResponse(BaseModel):
    """Body of every 422 response (documented in OpenAPI)."""

    code: str = "validation_error"
    detail: str
    errors: list[FieldError]


class NotFoundResponse(BaseModel):
    detail: str


class NotFound(Exception):
    """Base for domain "no such record" errors; each domain sets its own DETAIL."""

    DETAIL = "Not found"


def raise_validation_error(field: str, message: str, value: Any, location: str = "query") -> None:
    """Raise a 422 for `field`, formatted like FastAPI's own validation errors."""
    raise RequestValidationError([{"type": "value_error", "loc": (location, field), "msg": message, "input": value}])


def _field_error(err: dict[str, Any]) -> dict[str, Any]:
    loc = err.get("loc", ())
    # loc looks like ("query", "offset") or ("path", "device_id").
    location = loc[0] if loc else "request"
    field = ".".join(str(part) for part in loc[1:]) or location
    message = err.get("msg", "Invalid value")
    if err.get("type") == "string_pattern_mismatch":
        message = PATTERN_MESSAGES.get((err.get("ctx") or {}).get("pattern"), message)
    return {
        "field": field,
        "location": location,
        "message": message,
        "input": jsonable_encoder(err.get("input")),
    }


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """
    422 response naming each malformed field, e.g.

        {
          "code": "validation_error",
          "detail": "Invalid value for 'offset': Input should be greater than or equal to 0",
          "errors": [{"field": "offset", "location": "query",
                      "message": "Input should be greater than or equal to 0", "input": "-1"}]
        }
    """
    errors = [_field_error(e) for e in exc.errors()]
    fields = ", ".join(f"'{e['field']}'" for e in errors)
    if len(errors) == 1:
        detail = f"Invalid value for {fields}: {errors[0]['message']}"
    else:
        detail = f"Invalid values for {fields}"
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"code": "validation_error", "detail": detail, "errors": errors},
    )


async def not_found_handler(request: Request, exc: NotFound) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": exc.DETAIL})
