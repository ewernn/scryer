"""Unit tests for projects service."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.models.auth import ProjectMember
from scryer.server.models.enums import ProjectRole, ProjectVisibility
from scryer.server.services.projects import (
    archive_project,
    create_project,
    list_projects_for_user_in_workspace,
)
from scryer.server.services.users import create_user
from scryer.server.services.workspaces import create_workspace


async def _user(session: AsyncSession):
    return await create_user(session, email=f"u{uuid4().hex[:8]}@example.com", password="x" * 16)


async def _ws(session: AsyncSession, owner_id):
    return await create_workspace(
        session, slug=f"ws-{uuid4().hex[:8]}", name="W", owner_user_id=owner_id
    )


async def test_create_project_happy_path(session: AsyncSession) -> None:
    owner = await _user(session)
    ws = await _ws(session, owner.id)
    proj = await create_project(
        session, workspace_id=ws.id, slug="default", name="default", owner_user_id=owner.id
    )
    assert proj.workspace_id == ws.id
    assert proj.slug == "default"


async def test_workspace_member_sees_workspace_visible(session: AsyncSession) -> None:
    owner = await _user(session)
    ws = await _ws(session, owner.id)
    proj = await create_project(
        session,
        workspace_id=ws.id,
        slug="open",
        name="open",
        owner_user_id=owner.id,
        visibility=ProjectVisibility.workspace,
    )
    found = await list_projects_for_user_in_workspace(session, workspace_id=ws.id, user_id=owner.id)
    assert any(p.id == proj.id for p in found)


async def test_workspace_member_sees_private_only_if_explicit_member(
    session: AsyncSession,
) -> None:
    owner = await _user(session)
    member = await _user(session)
    ws = await _ws(session, owner.id)
    from scryer.server.models.auth import WorkspaceMember
    from scryer.server.models.enums import WorkspaceRole

    session.add(WorkspaceMember(workspace_id=ws.id, user_id=member.id, role=WorkspaceRole.member))
    await session.flush()
    private_proj = await create_project(
        session,
        workspace_id=ws.id,
        slug="secret",
        name="secret",
        owner_user_id=owner.id,
        visibility=ProjectVisibility.private,
    )
    found = await list_projects_for_user_in_workspace(
        session, workspace_id=ws.id, user_id=member.id
    )
    assert all(p.id != private_proj.id for p in found)
    session.add(
        ProjectMember(project_id=private_proj.id, user_id=member.id, role=ProjectRole.member)
    )
    await session.flush()
    found2 = await list_projects_for_user_in_workspace(
        session, workspace_id=ws.id, user_id=member.id
    )
    assert any(p.id == private_proj.id for p in found2)


async def test_non_member_sees_nothing(session: AsyncSession) -> None:
    owner = await _user(session)
    outsider = await _user(session)
    ws = await _ws(session, owner.id)
    await create_project(session, workspace_id=ws.id, slug="p1", name="p1", owner_user_id=owner.id)
    found = await list_projects_for_user_in_workspace(
        session, workspace_id=ws.id, user_id=outsider.id
    )
    assert found == []


async def test_archive_project_filters_out(session: AsyncSession) -> None:
    owner = await _user(session)
    ws = await _ws(session, owner.id)
    proj = await create_project(
        session, workspace_id=ws.id, slug="kill", name="kill", owner_user_id=owner.id
    )
    await archive_project(session, proj.id)
    found = await list_projects_for_user_in_workspace(session, workspace_id=ws.id, user_id=owner.id)
    assert all(p.id != proj.id for p in found)
