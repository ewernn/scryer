"""Workspace endpoints: list, get, archive."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.auth import Principal, get_principal
from scryer.server.db import apply_workspace_context, get_session
from scryer.server.models.enums import WorkspaceRole
from scryer.server.services.access import (
    assert_workspace_member,
    assert_workspace_role,
    set_user_context_dep,
)
from scryer.server.services.audit import write_event
from scryer.server.services.workspaces import (
    archive_workspace,
    get_workspace_by_slug,
    list_workspaces_for_user,
)

router = APIRouter(
    prefix="/workspaces",
    tags=["workspaces"],
    dependencies=[Depends(set_user_context_dep)],
)


class WorkspaceOut(BaseModel):
    id: str
    slug: str
    name: str
    created_at: datetime


@router.get(
    "",
    response_model=list[WorkspaceOut],
    operation_id="workspaces.list",
    summary="List workspaces accessible to the calling principal",
)
async def list_(
    principal: Annotated[Principal, Depends(get_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[WorkspaceOut]:
    rows = await list_workspaces_for_user(session, principal.id)
    return [
        WorkspaceOut(id=str(r.id), slug=r.slug, name=r.name, created_at=r.created_at) for r in rows
    ]


@router.get(
    "/{slug}",
    response_model=WorkspaceOut,
    operation_id="workspaces.get",
    summary="Get a workspace by slug",
)
async def get_(
    slug: str,
    principal: Annotated[Principal, Depends(get_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WorkspaceOut:
    ws = await get_workspace_by_slug(session, slug)
    await assert_workspace_member(session, principal, ws.id)
    return WorkspaceOut(id=str(ws.id), slug=ws.slug, name=ws.name, created_at=ws.created_at)


@router.delete(
    "/{slug}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="workspaces.archive",
    summary="Archive a workspace (soft-delete; cascades to children, cancels in-flight runs)",
)
async def archive_(
    slug: str,
    principal: Annotated[Principal, Depends(get_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Owner-only archive. Idempotent: re-archive returns 204 silently
    (the trigger WHEN clause makes the cascade a no-op on second call).

    Cascade behaviour: the DB trigger trg_workspace_cascade_archive (added
    in migration 9db64f8a534b) fans archived_at out to projects,
    service_accounts, credentials, budgets, webhooks. Versioned grandchildren
    (datasets, scorers, etc.) hide via the archived_at RLS predicate when
    their parent project is archived.

    The archive_workspace service also bulk-cancels in-flight runs
    (status='cancelled'). Wave 3 PID-tracking will make this enforceable
    for currently-executing workers; today the cancel is observed at the
    next cooperative status check between records."""
    ws = await get_workspace_by_slug(session, slug)
    await assert_workspace_role(session, principal, ws.id, min_role=WorkspaceRole.owner)
    # archive_workspace + cascade trigger touch SoftDelete RLS tables across
    # the workspace; the policy USING gates rows we're trying to UPDATE.
    # apply_workspace_context with include_archived=True ensures both
    # active and already-archived rows are visible (the cascade uses
    # `archived_at IS NULL` as its OWN guard against double-archive).
    await apply_workspace_context(
        session,
        ws.id,
        user_id=principal.id if principal.kind.value == "user" else None,
        include_archived=True,
    )
    await archive_workspace(session, ws.id)
    await write_event(
        session,
        action="workspace.archived",
        actor=principal,
        workspace_id=ws.id,
        resource_type="workspace",
        resource_id=ws.id,
    )
    await session.commit()
