"""Central RFC 9457 exception handler.

Maps every ScryerError subclass to a Problem response with `retryable` +
`suggestion` extension fields. Single registration on ScryerError catches
all subclasses via Starlette's MRO walk.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi_problem_details import ProblemResponse

from scryer.server.services.errors import (
    AuthError,
    ConflictError,
    IntegrityError,
    NotFoundError,
    PermissionError,
    ScryerError,
    ValidationError,
)

_BASE_URI = "https://scryer.io/errors"

_MAP: dict[type[ScryerError], tuple[int, str, str, str | None]] = {
    NotFoundError: (
        404,
        "not-found",
        "Resource Not Found",
        "Verify the resource ID with the list endpoint.",
    ),
    ConflictError: (409, "conflict", "Conflict", "Fetch the current state before retrying."),
    PermissionError: (403, "forbidden", "Forbidden", None),
    ValidationError: (422, "validation", "Validation Error", None),
    AuthError: (
        401,
        "unauthorized",
        "Unauthorized",
        "Re-authenticate and retry with a fresh token.",
    ),
    IntegrityError: (
        500,
        "integrity",
        "Internal Integrity Error",
        "This is a server bug; report the instance URI.",
    ),
}

_RETRYABLE: frozenset[type[ScryerError]] = frozenset({IntegrityError, AuthError})


async def scryer_error_handler(request: Request, exc: Exception) -> ProblemResponse:
    assert isinstance(exc, ScryerError)
    status_code, slug, title, suggestion = next(
        (v for cls, v in _MAP.items() if isinstance(exc, cls)),
        (500, "internal", "Internal Server Error", None),
    )
    extras: dict[str, Any] = {"retryable": type(exc) in _RETRYABLE}
    if suggestion:
        extras["suggestion"] = suggestion
    if isinstance(exc, NotFoundError):
        extras["problem_field"] = exc.resource

    return ProblemResponse(
        status=status_code,
        type=f"{_BASE_URI}/{slug}",
        title=title,
        detail=str(exc),
        instance=request.url.path,
        **extras,
    )


async def request_validation_handler(request: Request, exc: Exception) -> ProblemResponse:
    assert isinstance(exc, RequestValidationError)
    first = exc.errors()[0] if exc.errors() else {}
    return ProblemResponse(
        status=422,
        type=f"{_BASE_URI}/validation",
        title="Request Validation Error",
        detail=str(first.get("msg", "Invalid request")),
        instance=request.url.path,
        retryable=False,
        problem_field=".".join(str(p) for p in first.get("loc", [])),
    )
