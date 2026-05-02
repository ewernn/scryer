"""Phase 6 agent UX: /me + version headers."""

from __future__ import annotations

from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.services.users import create_user
from scryer.server.services.workspaces import create_workspace


async def _signup(http_session: AsyncSession) -> tuple[str, str]:
    email = f"u{uuid4().hex[:6]}@example.com"
    pw = "x" * 16
    user = await create_user(http_session, email=email, password=pw)
    await create_workspace(
        http_session, slug=f"w-{uuid4().hex[:6]}", name="t", owner_user_id=user.id
    )
    await http_session.commit()
    return email, pw


async def test_me_returns_identity_and_top_resources(
    client: AsyncClient, http_session: AsyncSession
) -> None:
    email, pw = await _signup(http_session)
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": pw})
    token = r.json()["access_token"]
    r = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["identity"]["kind"] == "user"
    assert body["api_version"] == "2026-05-02"
    assert body["docs"].startswith("https://")
    assert "workspaces" in body["top_resources"]
    assert len(body["top_resources"]["workspaces"]) >= 1


async def test_response_carries_version_headers(client: AsyncClient) -> None:
    r = await client.get("/api/v1/healthz")
    assert r.headers.get("Scryer-Version") == "2026-05-02"
    assert r.headers.get("Sunset") == "2027-05-02"
    assert "deprecation" in r.headers.get("Link", "")
    assert "X-Request-ID" in r.headers


async def test_me_requires_auth(client: AsyncClient) -> None:
    r = await client.get("/api/v1/me")
    assert r.status_code == 401
