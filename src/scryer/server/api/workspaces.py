"""Workspace endpoints: list, get."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.auth import Principal, get_principal
from scryer.server.db import get_session
from scryer.server.services.access import assert_workspace_member, set_user_context_dep
from scryer.server.services.workspaces import (
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
