"""Auth + cross-tenant integration tests. Pin down the critical findings
from Phase 1.10 critic so they don't regress."""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.services.users import create_user
from scryer.server.services.workspaces import create_workspace


async def _make_user_and_ws(http_session: AsyncSession) -> tuple[str, str, str]:
    email = f"u{uuid4().hex[:8]}@example.com"
    pw = "x" * 16
    user = await create_user(http_session, email=email, password=pw)
    ws = await create_workspace(
        http_session, slug=f"ws-{uuid4().hex[:8]}", name="t", owner_user_id=user.id
    )
    await http_session.commit()
    return email, pw, ws.slug


async def _login(client: AsyncClient, email: str, pw: str) -> str:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": pw})
    return r.json()["access_token"]


async def test_user_cannot_get_other_workspace(
    client: AsyncClient, http_session: AsyncSession
) -> None:
    """A's GET /workspaces/{B's slug} returns 404, not the workspace."""
    email_a, pw_a, _slug_a = await _make_user_and_ws(http_session)
    _email_b, _pw_b, slug_b = await _make_user_and_ws(http_session)
    token_a = await _login(client, email_a, pw_a)

    r = await client.get(
        f"/api/v1/workspaces/{slug_b}", headers={"Authorization": f"Bearer {token_a}"}
    )
    assert r.status_code == 404
    body = r.json()
    assert body["type"].endswith("/not-found")


async def test_user_cannot_list_projects_in_other_workspace(
    client: AsyncClient, http_session: AsyncSession
) -> None:
    email_a, pw_a, _ = await _make_user_and_ws(http_session)
    _, _, slug_b = await _make_user_and_ws(http_session)
    token_a = await _login(client, email_a, pw_a)

    r = await client.get(
        f"/api/v1/workspaces/{slug_b}/projects",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r.status_code == 404


@pytest.mark.parametrize(
    "header_value",
    ["", "Bearer", "Bearer ", "Basic abc123", "Bearer ..", "Bearer scrk_live_bogus"],
)
async def test_malformed_auth_returns_401_problem(client: AsyncClient, header_value: str) -> None:
    r = await client.get("/api/v1/workspaces", headers={"Authorization": header_value})
    assert r.status_code == 401
    assert r.headers.get("content-type", "").startswith("application/problem+json")


async def test_jwt_with_non_uuid_sub_returns_401_not_500(client: AsyncClient) -> None:
    """Critic flagged uuid.UUID(sub) raised ValueError → 500. Verify it's now 401."""
    from scryer.server.services.security import issue_access_jwt

    bad = issue_access_jwt("not-a-uuid")
    r = await client.get("/api/v1/workspaces", headers={"Authorization": f"Bearer {bad}"})
    assert r.status_code == 401
    assert r.headers.get("content-type", "").startswith("application/problem+json")
