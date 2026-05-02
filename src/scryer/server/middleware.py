"""Cross-cutting response headers per plan §12: Scryer-Version + Sunset."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

API_VERSION = "2026-05-02"
SUNSET_DATE = "2027-05-02"
DEPRECATION_DOC = "https://scryer.io/docs/migrations/2026-05-02"


class ApiVersionHeadersMiddleware(BaseHTTPMiddleware):
    """Plan §12: every response carries Scryer-Version + Sunset headers
    (RFC 8594 machine-readable for agent clients)."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["Scryer-Version"] = API_VERSION
        response.headers["Sunset"] = SUNSET_DATE
        response.headers["Link"] = f'<{DEPRECATION_DOC}>; rel="deprecation"'
        return response
