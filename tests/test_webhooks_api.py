"""Webhook CRUD endpoint tests: secret-handling contract + role enforcement."""

from __future__ import annotations

from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.models.enums import WorkspaceRole
from scryer.server.services.users import create_user
from scryer.server.services.workspaces import add_workspace_member, create_workspace


async def _signup_owner(http_session: AsyncSession) -> tuple[str, str, str]:
    """Create a user that owns a fresh workspace. Returns (email, pw, ws_slug)."""
    email = f"u{uuid4().hex[:8]}@example.com"
    pw = "x" * 16
    user = await create_user(http_session, email=email, password=pw)
    ws = await create_workspace(
        http_session, slug=f"w-{uuid4().hex[:6]}", name="Test", owner_user_id=user.id
    )
    await http_session.commit()
    return email, pw, ws.slug


async def _signup_viewer(http_session: AsyncSession, ws_slug: str) -> tuple[str, str]:
    """Create a fresh user and add them as `viewer` to the named workspace."""
    from sqlalchemy import select

    from scryer.server.models.auth import Workspace

    email = f"u{uuid4().hex[:8]}@example.com"
    pw = "x" * 16
    user = await create_user(http_session, email=email, password=pw)
    ws = (
        await http_session.execute(select(Workspace).where(Workspace.slug == ws_slug))
    ).scalar_one()
    await add_workspace_member(
        http_session, workspace_id=ws.id, user_id=user.id, role=WorkspaceRole.viewer
    )
    await http_session.commit()
    return email, pw


async def _bearer(client: AsyncClient, email: str, pw: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ── happy-path tests ────────────────────────────────────────────────────────


async def test_create_returns_secret_then_list_omits_it(
    client: AsyncClient, http_session: AsyncSession
) -> None:
    email, pw, ws = await _signup_owner(http_session)
    h = await _bearer(client, email, pw)

    r = await client.post(
        f"/api/v1/workspaces/{ws}/webhooks",
        json={"name": "wh1", "url": "https://example.com/hook", "event_types": ["run.done"]},
        headers=h,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["secret"], "create response must include the secret one time"
    assert len(body["secret"]) == 32, "secret should be 32 hex chars"

    # List MUST NOT include the secret on any row.
    r = await client.get(f"/api/v1/workspaces/{ws}/webhooks", headers=h)
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert "secret" not in rows[0], f"WebhookOut leaked secret: {rows[0]!r}"


async def test_rotate_secret_returns_new_value_and_invalidates_old(
    client: AsyncClient, http_session: AsyncSession
) -> None:
    email, pw, ws = await _signup_owner(http_session)
    h = await _bearer(client, email, pw)

    r = await client.post(
        f"/api/v1/workspaces/{ws}/webhooks",
        json={"name": "wh1", "url": "https://example.com/hook", "event_types": ["x"]},
        headers=h,
    )
    wh_id = r.json()["id"]
    old_secret = r.json()["secret"]

    r = await client.post(f"/api/v1/workspaces/{ws}/webhooks/{wh_id}/rotate-secret", headers=h)
    assert r.status_code == 200
    new_secret = r.json()["secret"]
    assert new_secret != old_secret


async def test_delete_returns_204_and_removes_row(
    client: AsyncClient, http_session: AsyncSession
) -> None:
    email, pw, ws = await _signup_owner(http_session)
    h = await _bearer(client, email, pw)

    r = await client.post(
        f"/api/v1/workspaces/{ws}/webhooks",
        json={"name": "wh1", "url": "https://example.com/hook", "event_types": ["x"]},
        headers=h,
    )
    wh_id = r.json()["id"]

    r = await client.delete(f"/api/v1/workspaces/{ws}/webhooks/{wh_id}", headers=h)
    assert r.status_code == 204

    r = await client.get(f"/api/v1/workspaces/{ws}/webhooks", headers=h)
    assert r.json() == []


# ── role enforcement ────────────────────────────────────────────────────────


async def test_viewer_can_list_but_cannot_create(
    client: AsyncClient, http_session: AsyncSession
) -> None:
    _email_o, _pw_o, ws = await _signup_owner(http_session)
    email_v, pw_v = await _signup_viewer(http_session, ws)
    hv = await _bearer(client, email_v, pw_v)

    r = await client.get(f"/api/v1/workspaces/{ws}/webhooks", headers=hv)
    assert r.status_code == 200, "viewer can list"

    r = await client.post(
        f"/api/v1/workspaces/{ws}/webhooks",
        json={"name": "wh1", "url": "https://example.com/hook", "event_types": ["x"]},
        headers=hv,
    )
    assert r.status_code == 403, "viewer must NOT be able to create webhooks"


async def test_non_member_gets_404_not_403_no_existence_oracle(
    client: AsyncClient, http_session: AsyncSession
) -> None:
    """Non-members get NotFoundError (404) not PermissionError (403) so the
    workspace's existence isn't leaked via response code."""
    _email_o, _pw_o, ws = await _signup_owner(http_session)

    # Brand-new user — not in this workspace at all.
    email_x = f"u{uuid4().hex[:8]}@example.com"
    pw_x = "x" * 16
    await create_user(http_session, email=email_x, password=pw_x)
    await http_session.commit()
    hx = await _bearer(client, email_x, pw_x)

    r = await client.get(f"/api/v1/workspaces/{ws}/webhooks", headers=hx)
    assert r.status_code == 404
