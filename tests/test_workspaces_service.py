"""Unit tests for workspaces service."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.services.errors import ConflictError
from scryer.server.services.users import create_user
from scryer.server.services.workspaces import (
    create_workspace,
    get_workspace_by_slug,
    list_workspaces_for_user,
    rename_workspace_slug,
)


async def _user(session: AsyncSession):
    return await create_user(session, email=f"u{uuid4().hex[:8]}@example.com", password="x" * 16)


async def test_create_workspace_happy_path(session: AsyncSession) -> None:
    user = await _user(session)
    slug = f"ws-{uuid4().hex[:8]}"
    ws = await create_workspace(session, slug=slug, name="My WS", owner_user_id=user.id)
    assert ws.slug == slug
    assert ws.owner_user_id == user.id
    found = await list_workspaces_for_user(session, user.id)
    assert any(w.id == ws.id for w in found)


async def test_create_workspace_bad_slug_conflict(session: AsyncSession) -> None:
    user = await _user(session)
    with pytest.raises(ConflictError):
        await create_workspace(session, slug="-bad-", name="x", owner_user_id=user.id)


async def test_create_workspace_duplicate_slug_conflict(session: AsyncSession) -> None:
    user = await _user(session)
    slug = f"ws-{uuid4().hex[:8]}"
    await create_workspace(session, slug=slug, name="A", owner_user_id=user.id)
    with pytest.raises(ConflictError):
        await create_workspace(session, slug=slug, name="B", owner_user_id=user.id)


async def test_create_workspace_slug_history_collision(session: AsyncSession) -> None:
    user = await _user(session)
    old = f"ws-{uuid4().hex[:8]}"
    new = f"ws-{uuid4().hex[:8]}"
    ws = await create_workspace(session, slug=old, name="A", owner_user_id=user.id)
    await rename_workspace_slug(session, ws.id, new)
    with pytest.raises(ConflictError):
        await create_workspace(session, slug=old, name="C", owner_user_id=user.id)


async def test_rename_workspace_slug_creates_redirect(session: AsyncSession) -> None:
    user = await _user(session)
    old = f"ws-{uuid4().hex[:8]}"
    new = f"ws-{uuid4().hex[:8]}"
    ws = await create_workspace(session, slug=old, name="A", owner_user_id=user.id)
    await rename_workspace_slug(session, ws.id, new)
    via_old = await get_workspace_by_slug(session, old)
    via_new = await get_workspace_by_slug(session, new)
    assert via_old.id == ws.id == via_new.id
    assert via_new.slug == new


async def test_get_workspace_by_slug_resolves_both(session: AsyncSession) -> None:
    user = await _user(session)
    slug = f"ws-{uuid4().hex[:8]}"
    ws = await create_workspace(session, slug=slug, name="A", owner_user_id=user.id)
    fetched = await get_workspace_by_slug(session, slug)
    assert fetched.id == ws.id


async def test_list_workspaces_for_user(session: AsyncSession) -> None:
    user = await _user(session)
    other = await _user(session)
    s1 = f"ws-{uuid4().hex[:8]}"
    s2 = f"ws-{uuid4().hex[:8]}"
    s3 = f"ws-{uuid4().hex[:8]}"
    a = await create_workspace(session, slug=s1, name="A", owner_user_id=user.id)
    b = await create_workspace(session, slug=s2, name="B", owner_user_id=user.id)
    await create_workspace(session, slug=s3, name="C", owner_user_id=other.id)
    found_ids = {w.id for w in await list_workspaces_for_user(session, user.id)}
    assert {a.id, b.id} <= found_ids
    other_ids = {w.id for w in await list_workspaces_for_user(session, other.id)}
    assert a.id not in other_ids
