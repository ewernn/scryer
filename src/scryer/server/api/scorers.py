"""Scorer endpoints: push, get latest, list."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.auth import Principal, get_principal
from scryer.server.db import get_session
from scryer.server.services.access import get_project_by_slug_path, require_workspace_from_path
from scryer.server.services.idempotency import (
    capture_idempotency_response,
    check_idempotency,
)
from scryer.server.services.scorers import (
    get_scorer_latest,
    list_scorers,
    push_scorer,
)

router = APIRouter(tags=["scorers"], dependencies=[Depends(require_workspace_from_path)])


class ScorerPushRequest(BaseModel):
    slug: str
    name: str
    source_text: str
    description: str | None = None
    server_executable: bool = True


class ScorerOut(BaseModel):
    id: str
    slug: str
    version: int
    content_hash: str
    name: str
    description: str | None
    server_executable: bool
    created_at: datetime


def _to_out(s: Any) -> ScorerOut:
    return ScorerOut(
        id=str(s.id),
        slug=s.slug,
        version=s.version,
        content_hash=s.content_hash,
        name=s.name,
        description=s.description,
        server_executable=s.server_executable,
        created_at=s.created_at,
    )


@router.post(
    "/workspaces/{workspace_slug}/projects/{project_slug}/scorers",
    response_model=ScorerOut,
    operation_id="scorers.push",
    dependencies=[Depends(check_idempotency)],
)
async def push(
    request: Request,
    workspace_slug: str,
    project_slug: str,
    body: ScorerPushRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
    background_tasks: BackgroundTasks,
) -> ScorerOut:
    proj = await get_project_by_slug_path(
        session, principal, workspace_slug=workspace_slug, project_slug=project_slug
    )
    s = await push_scorer(
        session,
        project_id=proj.id,
        slug=body.slug,
        name=body.name,
        source_text=body.source_text,
        description=body.description,
        server_executable=body.server_executable,
    )
    await session.commit()
    out = _to_out(s)
    capture_idempotency_response(
        background_tasks,
        request,
        getattr(request.app.state, "session_factory", None),
        status_code=200,
        body=out.model_dump(mode="json"),
    )
    return out


@router.get(
    "/workspaces/{workspace_slug}/projects/{project_slug}/scorers",
    response_model=list[ScorerOut],
    operation_id="scorers.list",
)
async def list_(
    workspace_slug: str,
    project_slug: str,
    principal: Annotated[Principal, Depends(get_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[ScorerOut]:
    proj = await get_project_by_slug_path(
        session, principal, workspace_slug=workspace_slug, project_slug=project_slug
    )
    rows = await list_scorers(session, project_id=proj.id)
    return [_to_out(r) for r in rows]


@router.get(
    "/workspaces/{workspace_slug}/projects/{project_slug}/scorers/{slug}",
    response_model=ScorerOut,
    operation_id="scorers.get_latest",
)
async def get_latest(
    workspace_slug: str,
    project_slug: str,
    slug: str,
    principal: Annotated[Principal, Depends(get_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ScorerOut:
    proj = await get_project_by_slug_path(
        session, principal, workspace_slug=workspace_slug, project_slug=project_slug
    )
    return _to_out(await get_scorer_latest(session, project_id=proj.id, slug=slug))
