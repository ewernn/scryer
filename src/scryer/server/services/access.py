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
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.auth import Principal, get_principal
from scryer.server.db import apply_workspace_context, get_session
from scryer.server.models.auth import (
    Project,
    ProjectMember,
    ServiceAccount,
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


async def _assert_sa_workspace(
    session: AsyncSession, principal: Principal, workspace_id: uuid.UUID
) -> None:
    """Verify the SA principal belongs to `workspace_id` AND that the
    workspace itself is still active (not archived).

    The SA's intrinsic workspace_id is carried on the Principal (set at
    AuthN time from api_keys.workspace_id) — no DB lookup needed for
    the SA-side check. Workspace.archived_at still requires a lookup
    since it can flip post-AuthN. Active-flag check on ServiceAccount
    requires a DB lookup too (could change post-AuthN if an admin
    deactivates mid-session).

    Raises NotFoundError on mismatch / archived to avoid existence oracle."""
    if principal.workspace_id != workspace_id:
        raise NotFoundError("workspace", str(workspace_id))
    ws = await session.get(Workspace, workspace_id)
    if ws is None or ws.archived_at is not None:
        raise NotFoundError("workspace", str(workspace_id))
    sa = await session.get(ServiceAccount, principal.id)
    if sa is None or sa.archived_at is not None:
        raise NotFoundError("workspace", str(workspace_id))
    if not sa.is_active:
        raise PermissionError("ServiceAccount is deactivated")


async def assert_workspace_member(
    session: AsyncSession, principal: Principal, workspace_id: uuid.UUID
) -> None:
    """Raises NotFoundError (not PermissionError) to avoid existence oracle.

    User principal: must have a WorkspaceMember row for this workspace.
    ServiceAccount principal: SA's intrinsic workspace_id must match."""
    if principal.kind == PrincipalKind.service_account:
        await _assert_sa_workspace(session, principal, workspace_id)
        return
    row = await session.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == principal.id,
        )
    )
    if row.scalar_one_or_none() is None:
        raise NotFoundError("workspace", str(workspace_id))


# Effective WorkspaceRole rank for ServiceAccount principals — they pass
# member-level checks but cannot satisfy owner-level ones (workspace
# admin operations like deleting members or the workspace itself stay
# user-only). The api_key's scopes still constrain what an SA can do
# even within member-rank operations.
_SA_EFFECTIVE_RANK = _ROLE_RANK[WorkspaceRole.member]


async def assert_workspace_role(
    session: AsyncSession,
    principal: Principal,
    workspace_id: uuid.UUID,
    *,
    min_role: WorkspaceRole,
) -> WorkspaceMember | None:
    """Like assert_workspace_member, but also requires the member's role to be
    at least `min_role` (viewer < member < owner).

    Returns the WorkspaceMember row for User principals; None for SA
    principals (no membership row — SA effective rank is `member`).

    Membership-not-found raises NotFoundError (no oracle); insufficient role
    raises PermissionError (the principal IS in the workspace, just lacks
    privilege — masking that as "not found" would be confusing)."""
    if principal.kind == PrincipalKind.service_account:
        await _assert_sa_workspace(session, principal, workspace_id)
        if _SA_EFFECTIVE_RANK < _ROLE_RANK[min_role]:
            raise PermissionError(
                f"Requires workspace role {min_role.value!r} "
                f"(ServiceAccount effective rank: 'member')"
            )
        return None
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
    """Returns the Project iff principal can read it.

    User principal — visibility-aware: `workspace`-projects accessible to
    all workspace members; `private` requires explicit ProjectMember.

    SA principal — SA can read all `workspace`-visibility projects in
    its workspace. Cannot access private projects (no SA-project-membership
    concept — keep that for v2 if needed)."""
    proj = await session.get(Project, project_id)
    if proj is None or proj.archived_at is not None:
        raise NotFoundError("project", str(project_id))

    if principal.kind == PrincipalKind.service_account:
        await _assert_sa_workspace(session, principal, proj.workspace_id)
        if proj.visibility == ProjectVisibility.private:
            raise NotFoundError("project", str(project_id))
        return proj

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

    Sets BOTH GUCs the RLS listener cares about:
      - app.current_workspace_id from `request.state.workspace_id`
      - app.current_user_id from `request.state.current_user_id`
        (only when principal.kind == user; SA-keyed flows go through
        the workspace_id check on api_keys policy and don't need the
        user GUC).
    """
    from scryer.server.services.workspaces import get_workspace_by_slug

    ws = await get_workspace_by_slug(session, workspace_slug)
    request.state.workspace_id = ws.id
    if principal.kind == PrincipalKind.user:
        request.state.current_user_id = principal.id
        await apply_workspace_context(session, ws.id, user_id=principal.id)
    else:
        await apply_workspace_context(session, ws.id)
    await assert_workspace_member(session, principal, ws.id)
    return ws.id


async def set_user_context_dep(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
) -> Principal:
    """FastAPI dep for routes WITHOUT workspace context but WITH a user
    principal (auth, /me). Sets only `app.current_user_id` so policies on
    workspace_members / project_members can permit the user's own rows.

    Apply at router level the same way as `require_workspace_from_path`.
    For SA principals it's a no-op — they don't have a user identity to
    project, and routes that need SA access should resolve through a
    workspace path instead."""
    if principal.kind == PrincipalKind.user:
        request.state.current_user_id = principal.id
        session.info["current_user_id"] = principal.id
        await session.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": str(principal.id)},
        )
    return principal


async def get_project_by_slug_path(
    session: AsyncSession,
    principal: Principal,
    *,
    workspace_slug: str,
    project_slug: str,
) -> Project:
    """Single resolver: (ws_slug, proj_slug) → Project, with full authz check.

    Sets RLS context to the resolved workspace (workspaces table itself
    isn't RLS-policied so the initial slug lookup works regardless).
    Subsequent service calls in the same request — push_dataset etc. —
    inherit the GUC."""
    ws = await session.execute(
        select(Workspace).where(Workspace.slug == workspace_slug, Workspace.archived_at.is_(None))
    )
    ws_row = ws.scalar_one_or_none()
    if ws_row is None:
        raise NotFoundError("workspace", workspace_slug)
    user_id = principal.id if principal.kind == PrincipalKind.user else None
    await apply_workspace_context(session, ws_row.id, user_id=user_id)
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
