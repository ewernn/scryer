"""Invitation API tests: owner-only create + signup with token redeems."""

from __future__ import annotations

from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.services.users import create_user
from scryer.server.services.workspaces import create_workspace


async def _signup_owner(http_session: AsyncSession) -> tuple[str, str, str]:
    email = f"u{uuid4().hex[:8]}@example.com"
    pw = "x" * 16
    user = await create_user(http_session, email=email, password=pw)
    ws = await create_workspace(
        http_session, slug=f"w-{uuid4().hex[:6]}", name="Test", owner_user_id=user.id
    )
    await http_session.commit()
    return email, pw, ws.slug


async def _bearer(client: AsyncClient, email: str, pw: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_owner_creates_invite_then_invitee_signs_up(
    client: AsyncClient, http_session: AsyncSession
) -> None:
    email_o, pw_o, ws = await _signup_owner(http_session)
    h = await _bearer(client, email_o, pw_o)

    invitee_email = f"new{uuid4().hex[:6]}@example.com"
    r = await client.post(
        f"/api/v1/workspaces/{ws}/invitations",
        json={"email": invitee_email, "workspace_role": "member", "ttl_days": 7},
        headers=h,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["token"], "create response must include the raw token once"
    token = body["token"]

    # Invitee signs up using the token.
    r = await client.post(
        "/api/v1/auth/signup",
        json={
            "token": token,
            "email": invitee_email,
            "password": "y" * 16,
        },
    )
    assert r.status_code == 201, r.text
    sb = r.json()
    assert sb["access_token"]
    assert str(sb["workspace_id"]) == body["workspace_id"]


async def test_signup_wrong_email_rejected(client: AsyncClient, http_session: AsyncSession) -> None:
    email_o, pw_o, ws = await _signup_owner(http_session)
    h = await _bearer(client, email_o, pw_o)

    pinned = f"pinned{uuid4().hex[:6]}@example.com"
    r = await client.post(
        f"/api/v1/workspaces/{ws}/invitations",
        json={"email": pinned, "workspace_role": "member"},
        headers=h,
    )
    token = r.json()["token"]

    # Wrong email — must be rejected.
    r = await client.post(
        "/api/v1/auth/signup",
        json={
            "token": token,
            "email": f"different{uuid4().hex[:6]}@example.com",
            "password": "y" * 16,
        },
    )
    assert r.status_code == 401  # AuthError → RFC 9457 401


async def test_non_owner_cannot_create_invite(
    client: AsyncClient, http_session: AsyncSession
) -> None:
    """Brand-new user with no workspace membership gets 404 (no oracle), not 403."""
    _email_o, _pw_o, ws = await _signup_owner(http_session)

    email_x = f"u{uuid4().hex[:8]}@example.com"
    pw_x = "x" * 16
    await create_user(http_session, email=email_x, password=pw_x)
    await http_session.commit()
    hx = await _bearer(client, email_x, pw_x)

    r = await client.post(
        f"/api/v1/workspaces/{ws}/invitations",
        json={"email": "x@example.com", "workspace_role": "member"},
        headers=hx,
    )
    assert r.status_code == 404


async def test_token_reuse_rejected(client: AsyncClient, http_session: AsyncSession) -> None:
    """Once a token is used, second signup attempt with same token must fail."""
    email_o, pw_o, ws = await _signup_owner(http_session)
    h = await _bearer(client, email_o, pw_o)

    invitee = f"once{uuid4().hex[:6]}@example.com"
    r = await client.post(
        f"/api/v1/workspaces/{ws}/invitations",
        json={"email": invitee, "workspace_role": "member"},
        headers=h,
    )
    token = r.json()["token"]

    r = await client.post(
        "/api/v1/auth/signup",
        json={"token": token, "email": invitee, "password": "y" * 16},
    )
    assert r.status_code == 201

    # Second use must fail (used_at set on first redemption).
    r = await client.post(
        "/api/v1/auth/signup",
        json={
            "token": token,
            "email": f"second{uuid4().hex[:6]}@example.com",
            "password": "z" * 16,
        },
    )
    assert r.status_code == 401
