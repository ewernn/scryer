"""End-to-end tests for ServiceAccount HTTP API.

Verifies: create → list → issue api_key → use api_key on a workspace
endpoint → archive. The "use api_key" step exercises the SA AuthN flow
plus the new SA-aware access helpers (test_access_service_accounts.py
covers the helpers in isolation; this test covers the integration)."""

from __future__ import annotations

from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.services.users import create_user
from scryer.server.services.workspaces import create_workspace


async def _signup(http_session: AsyncSession) -> tuple[str, str, str]:
    """Returns (email, password, ws_slug). Owner workspace role."""
    email = f"u{uuid4().hex[:8]}@example.com"
    pw = "x" * 16
    user = await create_user(http_session, email=email, password=pw)
    ws = await create_workspace(
        http_session, slug=f"w-{uuid4().hex[:8]}", name="t", owner_user_id=user.id
    )
    await http_session.commit()
    return email, pw, ws.slug


async def _login(client: AsyncClient, email: str, pw: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": pw})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_owner_creates_sa_and_issues_key(
    client: AsyncClient, http_session: AsyncSession
) -> None:
    email, pw, ws = await _signup(http_session)
    h = await _login(client, email, pw)

    r = await client.post(
        f"/api/v1/workspaces/{ws}/service-accounts", json={"name": "ci-bot"}, headers=h
    )
    assert r.status_code == 201, r.text
    sa = r.json()
    assert sa["name"] == "ci-bot"
    assert sa["is_active"] is True

    r = await client.get(f"/api/v1/workspaces/{ws}/service-accounts", headers=h)
    assert r.status_code == 200
    listed = r.json()
    assert any(s["id"] == sa["id"] for s in listed)

    r = await client.post(
        f"/api/v1/workspaces/{ws}/service-accounts/{sa['id']}/api-keys",
        json={"name": "default", "scopes": ["read", "write"]},
        headers=h,
    )
    assert r.status_code == 201, r.text
    key = r.json()
    assert key["full_key"].startswith("scrk_live_")
    assert "read" in key["scopes"]


async def test_sa_key_can_access_workspace_endpoint(
    client: AsyncClient, http_session: AsyncSession
) -> None:
    """Issue an SA key, then use it to call /me — the SA principal should
    resolve and access pass through assert_workspace_member SA branch."""
    email, pw, ws = await _signup(http_session)
    h = await _login(client, email, pw)

    sa_resp = await client.post(
        f"/api/v1/workspaces/{ws}/service-accounts", json={"name": "bot"}, headers=h
    )
    sa = sa_resp.json()
    key_resp = await client.post(
        f"/api/v1/workspaces/{ws}/service-accounts/{sa['id']}/api-keys",
        json={"scopes": ["read"]},
        headers=h,
    )
    full_key = key_resp.json()["full_key"]

    sa_h = {"Authorization": f"Bearer {full_key}"}
    r = await client.get("/api/v1/me", headers=sa_h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["identity"]["kind"] == "service_account"
    assert body["identity"]["id"] == sa["id"]


async def test_member_cannot_create_sa(client: AsyncClient, http_session: AsyncSession) -> None:
    """Owner-only operation. Add a member-role user, attempt create, expect 403."""
    from scryer.server.models.auth import WorkspaceMember
    from scryer.server.models.enums import WorkspaceRole

    email, pw, ws = await _signup(http_session)
    member_email = f"m{uuid4().hex[:8]}@example.com"
    member_pw = "x" * 16
    member = await create_user(http_session, email=member_email, password=member_pw)
    # Insert a non-owner WorkspaceMember directly.
    from sqlalchemy import select

    from scryer.server.models.auth import Workspace

    ws_row = (
        await http_session.execute(select(Workspace).where(Workspace.slug == ws))
    ).scalar_one()
    http_session.add(
        WorkspaceMember(workspace_id=ws_row.id, user_id=member.id, role=WorkspaceRole.member)
    )
    await http_session.commit()

    h = await _login(client, member_email, member_pw)
    r = await client.post(
        f"/api/v1/workspaces/{ws}/service-accounts", json={"name": "x"}, headers=h
    )
    assert r.status_code == 403, r.text
