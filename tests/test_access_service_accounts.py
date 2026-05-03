"""ServiceAccount support in access helpers (assert_workspace_member,
assert_workspace_role, assert_project_access).

Tests use synthetic Principal objects directly rather than going through
the AuthN flow — the goal is to verify the policy logic in services/access.py."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.auth import Principal
from scryer.server.models.enums import (
    ApiScope,
    PrincipalKind,
    ProjectVisibility,
    WorkspaceRole,
)
from scryer.server.services.access import (
    assert_project_access,
    assert_workspace_member,
    assert_workspace_role,
)
from scryer.server.services.errors import NotFoundError, PermissionError
from scryer.server.services.projects import create_project
from scryer.server.services.service_accounts import create_service_account
from scryer.server.services.users import create_user
from scryer.server.services.workspaces import create_workspace


def _sa_principal(sa_id) -> Principal:
    return Principal(
        id=sa_id,
        kind=PrincipalKind.service_account,
        scopes=frozenset({ApiScope.read, ApiScope.write}),
        api_key_id=uuid4(),
    )


async def _user_and_ws(session):
    user = await create_user(session, email=f"u{uuid4().hex[:6]}@e.com", password="x" * 16)
    ws = await create_workspace(
        session, slug=f"w-{uuid4().hex[:6]}", name="W", owner_user_id=user.id
    )
    return user, ws


async def test_sa_member_check_passes_for_own_workspace(session: AsyncSession) -> None:
    user, ws = await _user_and_ws(session)
    sa = await create_service_account(session, workspace_id=ws.id, name="bot")
    await assert_workspace_member(session, _sa_principal(sa.id), ws.id)  # no raise


async def test_sa_member_check_rejects_foreign_workspace(session: AsyncSession) -> None:
    _, ws_a = await _user_and_ws(session)
    _, ws_b = await _user_and_ws(session)
    sa = await create_service_account(session, workspace_id=ws_a.id, name="bot")
    with pytest.raises(NotFoundError):
        await assert_workspace_member(session, _sa_principal(sa.id), ws_b.id)


async def test_sa_role_check_member_passes_owner_fails(session: AsyncSession) -> None:
    user, ws = await _user_and_ws(session)
    sa = await create_service_account(session, workspace_id=ws.id, name="bot")
    p = _sa_principal(sa.id)
    # member-level: passes
    await assert_workspace_role(session, p, ws.id, min_role=WorkspaceRole.member)
    # owner-level: rejects (SA effective rank is 'member')
    with pytest.raises(PermissionError):
        await assert_workspace_role(session, p, ws.id, min_role=WorkspaceRole.owner)


async def test_sa_project_access_ok_for_workspace_visible(session: AsyncSession) -> None:
    user, ws = await _user_and_ws(session)
    sa = await create_service_account(session, workspace_id=ws.id, name="bot")
    proj = await create_project(
        session,
        workspace_id=ws.id,
        slug="default",
        name="P",
        owner_user_id=user.id,
        visibility=ProjectVisibility.workspace,
    )
    proj_returned = await assert_project_access(session, _sa_principal(sa.id), proj.id)
    assert proj_returned.id == proj.id


async def test_sa_project_access_blocked_for_private(session: AsyncSession) -> None:
    user, ws = await _user_and_ws(session)
    sa = await create_service_account(session, workspace_id=ws.id, name="bot")
    proj = await create_project(
        session,
        workspace_id=ws.id,
        slug="priv",
        name="P",
        owner_user_id=user.id,
        visibility=ProjectVisibility.private,
    )
    with pytest.raises(NotFoundError):
        await assert_project_access(session, _sa_principal(sa.id), proj.id)


async def test_sa_deactivated_raises(session: AsyncSession) -> None:
    user, ws = await _user_and_ws(session)
    sa = await create_service_account(session, workspace_id=ws.id, name="bot")
    sa.is_active = False
    await session.flush()
    with pytest.raises(PermissionError):
        await assert_workspace_member(session, _sa_principal(sa.id), ws.id)
