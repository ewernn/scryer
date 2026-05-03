"""Central access-control helpers + FastAPI dependency for workspace context.

Use these from API endpoints — never roll your own membership/scope checks
per-handler. The `require_workspace_from_path` dep, applied at the router
level for any route with `{workspace_slug}` in the URL, resolves the
workspace AND sets `request.state.workspace_id` + `session.info["workspace_id"]`
so the RLS listener (db.py) sees it on the NEXT transaction. This is the
structural enforcement layer — handlers can't forget to set workspace
context."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.auth import Principal, get_principal
from scryer.server.db import get_session
from scryer.server.models.auth import (
    Project,
    ProjectMember,
    Workspace,
    WorkspaceMember,
)
from scryer.server.models.enums import PrincipalKind, ProjectVisibility, WorkspaceRole
from scryer.server.services.errors import NotFoundError, PermissionError

# WorkspaceRole rank — higher number = more authority. Used by
# assert_workspace_role to compare a member's role against a required minimum.
_ROLE_RANK = {
    WorkspaceRole.viewer: 0,
    WorkspaceRole.member: 1,
    WorkspaceRole.owner: 2,
}


async def assert_workspace_member(
    session: AsyncSession, principal: Principal, workspace_id: uuid.UUID
) -> None:
    """Raises NotFoundError (not PermissionError) to avoid existence oracle."""
    if principal.kind != PrincipalKind.user:
        # ServiceAccounts are workspace-scoped; resolve via api_key principal_id
        # path; not yet supported here. v1 once SA-scoped resources land.
        raise PermissionError("ServiceAccount workspace access not yet supported")
    row = await session.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == principal.id,
        )
    )
    if row.scalar_one_or_none() is None:
        raise NotFoundError("workspace", str(workspace_id))


async def assert_workspace_role(
    session: AsyncSession,
    principal: Principal,
    workspace_id: uuid.UUID,
    *,
    min_role: WorkspaceRole,
) -> WorkspaceMember:
    """Like assert_workspace_member, but also requires the member's role to be
    at least `min_role` (viewer < member < owner). Returns the member row.

    Membership-not-found raises NotFoundError (no oracle); insufficient role
    raises PermissionError (the principal IS in the workspace, just lacks
    privilege — masking that as "not found" would be confusing)."""
    if principal.kind != PrincipalKind.user:
        raise PermissionError("ServiceAccount workspace access not yet supported")
    row = await session.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == principal.id,
        )
    )
    member = row.scalar_one_or_none()
    if member is None:
        raise NotFoundError("workspace", str(workspace_id))
    if _ROLE_RANK[member.role] < _ROLE_RANK[min_role]:
        raise PermissionError(
            f"Requires workspace role {min_role.value!r} (have {member.role.value!r})"
        )
    return member


async def assert_project_access(
    session: AsyncSession, principal: Principal, project_id: uuid.UUID
) -> Project:
    """Returns the Project iff principal can read it. Visibility-aware:
    `workspace`-projects accessible to all workspace members; `private` requires
    explicit project membership. Raises NotFoundError on no access."""
    proj = await session.get(Project, project_id)
    if proj is None or proj.archived_at is not None:
        raise NotFoundError("project", str(project_id))

    if principal.kind != PrincipalKind.user:
        raise PermissionError("ServiceAccount project access not yet supported")

    ws_member = await session.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == proj.workspace_id,
            WorkspaceMember.user_id == principal.id,
        )
    )
    if ws_member.scalar_one_or_none() is None:
        raise NotFoundError("project", str(project_id))

    if proj.visibility == ProjectVisibility.private:
        proj_member = await session.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == principal.id,
            )
        )
        if proj_member.scalar_one_or_none() is None:
            raise NotFoundError("project", str(project_id))

    return proj


async def require_workspace_from_path(
    workspace_slug: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
) -> uuid.UUID:
    """FastAPI dep: resolve workspace from `{workspace_slug}` path param,
    assert principal is a member, set RLS context for subsequent queries.

    Apply at router level via:
        router = APIRouter(
            prefix="/workspaces",
            dependencies=[Depends(require_workspace_from_path)],
        )
    or per-route on routers that mix workspace-scoped + global routes.

    Sets BOTH:
      - request.state.workspace_id  → consumed by future Depends/middleware
      - session.info["workspace_id"] → consumed by RLS listener at next tx
    """
    from scryer.server.services.workspaces import get_workspace_by_slug

    ws = await get_workspace_by_slug(session, workspace_slug)
    await assert_workspace_member(session, principal, ws.id)
    request.state.workspace_id = ws.id
    session.info["workspace_id"] = ws.id
    return ws.id


async def get_project_by_slug_path(
    session: AsyncSession,
    principal: Principal,
    *,
    workspace_slug: str,
    project_slug: str,
) -> Project:
    """Single resolver: (ws_slug, proj_slug) → Project, with full authz check."""
    ws = await session.execute(
        select(Workspace).where(Workspace.slug == workspace_slug, Workspace.archived_at.is_(None))
    )
    ws_row = ws.scalar_one_or_none()
    if ws_row is None:
        raise NotFoundError("workspace", workspace_slug)
    await assert_workspace_member(session, principal, ws_row.id)

    proj = await session.execute(
        select(Project).where(
            Project.workspace_id == ws_row.id,
            Project.slug == project_slug,
            Project.archived_at.is_(None),
        )
    )
    proj_row = proj.scalar_one_or_none()
    if proj_row is None:
        raise NotFoundError("project", f"{workspace_slug}/{project_slug}")
    return await assert_project_access(session, principal, proj_row.id)
