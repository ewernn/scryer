"""Project endpoints: list within a workspace."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.auth import Principal, get_principal
from scryer.server.db import get_session
from scryer.server.models.enums import ProjectVisibility
from scryer.server.services.access import assert_workspace_member
from scryer.server.services.projects import list_projects_for_user_in_workspace
from scryer.server.services.workspaces import get_workspace_by_slug

router = APIRouter(prefix="/workspaces", tags=["projects"])


class ProjectOut(BaseModel):
    id: str
    slug: str
    name: str
    visibility: ProjectVisibility
    created_at: datetime


@router.get(
    "/{workspace_slug}/projects",
    response_model=list[ProjectOut],
    operation_id="projects.list",
    summary="List projects in a workspace visible to the calling principal",
)
async def list_(
    workspace_slug: str,
    principal: Annotated[Principal, Depends(get_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[ProjectOut]:
    ws = await get_workspace_by_slug(session, workspace_slug)
    await assert_workspace_member(session, principal, ws.id)
    rows = await list_projects_for_user_in_workspace(
        session, workspace_id=ws.id, user_id=principal.id
    )
    return [
        ProjectOut(
            id=str(r.id),
            slug=r.slug,
            name=r.name,
            visibility=r.visibility,
            created_at=r.created_at,
        )
        for r in rows
    ]
