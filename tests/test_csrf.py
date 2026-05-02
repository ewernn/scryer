"""CSRF protection on /web/* POST endpoints.

Pattern: HMAC-SHA256(jwt_secret, session_jwt). Stateless verification —
server recomputes from the session cookie and constant-time compares.
"""

from __future__ import annotations

from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.services.security import generate_csrf
from scryer.server.services.users import create_user
from scryer.server.services.workspaces import create_workspace


async def _login_and_get_session(
    client: AsyncClient, http_session: AsyncSession
) -> tuple[str, str]:
    """Returns (session_cookie_value, csrf_token)."""
    email = f"u{uuid4().hex[:8]}@example.com"
    pw = "x" * 16
    user = await create_user(http_session, email=email, password=pw)
    await create_workspace(
        http_session, slug=f"w-{uuid4().hex[:6]}", name="W", owner_user_id=user.id
    )
    await http_session.commit()

    r = await client.post(
        "/web/login", data={"email": email, "password": pw}, follow_redirects=False
    )
    assert r.status_code == 303
    cookie = r.cookies["scryer_session"]
    csrf = generate_csrf(cookie)
    return cookie, csrf


async def test_logout_without_csrf_returns_403(
    client: AsyncClient, http_session: AsyncSession
) -> None:
    cookie, _csrf = await _login_and_get_session(client, http_session)
    r = await client.post(
        "/web/logout",
        cookies={"scryer_session": cookie},
        follow_redirects=False,
    )
    # No `csrf` form field at all — Form()-required, 422 from FastAPI
    # validation OR 403 from the handler. Either is acceptable rejection.
    assert r.status_code in (403, 422), f"unexpected {r.status_code}: {r.text}"


async def test_logout_with_wrong_csrf_returns_403(
    client: AsyncClient, http_session: AsyncSession
) -> None:
    cookie, _csrf = await _login_and_get_session(client, http_session)
    r = await client.post(
        "/web/logout",
        data={"csrf": "definitely-not-a-real-token-" + "a" * 60},
        cookies={"scryer_session": cookie},
        follow_redirects=False,
    )
    assert r.status_code == 403


async def test_logout_with_valid_csrf_succeeds(
    client: AsyncClient, http_session: AsyncSession
) -> None:
    cookie, csrf = await _login_and_get_session(client, http_session)
    r = await client.post(
        "/web/logout",
        data={"csrf": csrf},
        cookies={"scryer_session": cookie},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/web/login"
