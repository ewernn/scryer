"""BodySizeLimitMiddleware: 413 on oversize requests via Content-Length and
streaming-body checks."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


async def test_oversize_content_length_rejected_413(client: AsyncClient) -> None:
    """The middleware should fast-path-reject by Content-Length without ever
    invoking the route handler."""
    big = b"x" * (10 * 1024 * 1024 + 1)  # 10 MiB + 1 byte
    # Hit a real POST endpoint — login is convenient and unauthenticated.
    r = await client.post(
        "/api/v1/auth/login",
        content=big,
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 413
    body = r.json()
    assert body["status"] == 413
    assert "exceeds" in body["detail"].lower()


async def test_under_limit_request_passes_through(client: AsyncClient) -> None:
    """Sanity check: a small request still reaches the handler (which then
    rejects on its own merits — 401 for bad creds, not 413)."""
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": "x" * 16},
    )
    # Either 401 (auth failure — handler ran) or 422 (validation). NEVER 413.
    assert r.status_code != 413


@pytest.mark.parametrize("scheme", ["GET", "OPTIONS"])
async def test_get_options_unaffected(client: AsyncClient, scheme: str) -> None:
    """Methods without bodies must not be rejected."""
    r = await client.request(scheme, "/api/v1/healthz")
    assert r.status_code != 413
