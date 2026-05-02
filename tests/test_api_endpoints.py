"""HTTP integration tests: auth, workspace list, project list."""

from __future__ import annotations

from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.services.users import create_user
from scryer.server.services.workspaces import create_workspace


async def _signup(http_session: AsyncSession) -> tuple[str, str, str]:
    """Signup helper. Uses unique email/slug to avoid cross-test collisions
    (HTTP tests share a schema and can't rollback per-test)."""
    email = f"u{uuid4().hex[:8]}@example.com"
    password = "x" * 16
    user = await create_user(http_session, email=email, password=password)
    ws = await create_workspace(
        http_session, slug=f"ws-{uuid4().hex[:8]}", name="Test", owner_user_id=user.id
    )
    await http_session.commit()
    return email, password, ws.slug


async def test_login_and_me(client: AsyncClient, http_session: AsyncSession) -> None:
    email, password, _ = await _signup(http_session)
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]

    r = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "user"
    assert "read" in body["scopes"]


async def test_login_bad_password_returns_401_problem(
    client: AsyncClient, http_session: AsyncSession
) -> None:
    email, _, _ = await _signup(http_session)
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "wrong"})
    assert r.status_code == 401
    assert r.headers.get("content-type", "").startswith("application/problem+json")
    body = r.json()
    assert body["type"].endswith("/unauthorized")
    assert body["retryable"] is True


async def test_workspaces_list_requires_auth(client: AsyncClient) -> None:
    r = await client.get("/api/v1/workspaces")
    assert r.status_code == 401


async def test_workspaces_and_projects_endpoints(
    client: AsyncClient, http_session: AsyncSession
) -> None:
    email, password, ws_slug = await _signup(http_session)
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    token = r.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}

    r = await client.get("/api/v1/workspaces", headers=h)
    assert r.status_code == 200
    slugs = [w["slug"] for w in r.json()]
    assert ws_slug in slugs

    r = await client.get(f"/api/v1/workspaces/{ws_slug}", headers=h)
    assert r.status_code == 200
    assert r.json()["slug"] == ws_slug

    r = await client.get(f"/api/v1/workspaces/{ws_slug}/projects", headers=h)
    assert r.status_code == 200


async def test_get_unknown_workspace_returns_404_problem(
    client: AsyncClient, http_session: AsyncSession
) -> None:
    email, password, _ = await _signup(http_session)
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    token = r.json()["access_token"]

    r = await client.get(
        "/api/v1/workspaces/does-not-exist-xyz",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404
    body = r.json()
    assert body["type"].endswith("/not-found")
    assert body["retryable"] is False
