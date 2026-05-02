"""Phase 5 dashboard smoke tests."""

from __future__ import annotations

from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.services.users import create_user
from scryer.server.services.workspaces import create_workspace


async def _signup(http_session: AsyncSession) -> tuple[str, str, str]:
    email = f"u{uuid4().hex[:6]}@example.com"
    pw = "x" * 16
    user = await create_user(http_session, email=email, password=pw)
    ws = await create_workspace(
        http_session, slug=f"w-{uuid4().hex[:6]}", name="Test", owner_user_id=user.id
    )
    await http_session.commit()
    return email, pw, ws.slug


async def test_login_form_renders(client: AsyncClient) -> None:
    r = await client.get("/web/login")
    assert r.status_code == 200
    assert "Sign in to scryer" in r.text


async def test_workspaces_redirects_when_not_logged_in(client: AsyncClient) -> None:
    r = await client.get("/web/workspaces", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/web/login"


async def test_root_redirects_to_workspaces(client: AsyncClient) -> None:
    r = await client.get("/web", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/web/workspaces"


async def test_login_then_workspaces(client: AsyncClient, http_session: AsyncSession) -> None:
    email, pw, ws_slug = await _signup(http_session)
    r = await client.post(
        "/web/login", data={"email": email, "password": pw}, follow_redirects=False
    )
    assert r.status_code == 303
    assert r.cookies.get("scryer_session")
    cookie = r.cookies["scryer_session"]
    r = await client.get("/web/workspaces", cookies={"scryer_session": cookie})
    assert r.status_code == 200
    assert ws_slug in r.text
