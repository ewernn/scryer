"""Task endpoints: bind, get latest, list."""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.auth import Principal, get_principal
from scryer.server.db import get_session
from scryer.server.services.access import get_project_by_slug_path
from scryer.server.services.tasks import (
    get_task_latest,
    list_tasks,
    push_task,
)

router = APIRouter(tags=["tasks"])


class TaskPushRequest(BaseModel):
    slug: str
    name: str
    dataset_id: str
    dataset_version: int
    scorer_id: str
    scorer_version: int
    agent_id: str | None = None
    agent_version: int | None = None
    prompt_id: str | None = None
    prompt_version: int | None = None
    params_json: dict[str, Any] | None = None
    estimated_cost_usd: Decimal | None = None
    description: str | None = None


class TaskOut(BaseModel):
    id: str
    slug: str
    version: int
    content_hash: str
    name: str
    dataset_id: str
    dataset_version: int
    scorer_id: str
    scorer_version: int
    agent_id: str | None
    agent_version: int | None
    prompt_id: str | None
    prompt_version: int | None
    created_at: datetime


def _to_out(t: Any) -> TaskOut:
    return TaskOut(
        id=str(t.id),
        slug=t.slug,
        version=t.version,
        content_hash=t.content_hash,
        name=t.name,
        dataset_id=str(t.dataset_id),
        dataset_version=t.dataset_version,
        scorer_id=str(t.scorer_id),
        scorer_version=t.scorer_version,
        agent_id=str(t.agent_id) if t.agent_id else None,
        agent_version=t.agent_version,
        prompt_id=str(t.prompt_id) if t.prompt_id else None,
        prompt_version=t.prompt_version,
        created_at=t.created_at,
    )


@router.post(
    "/workspaces/{workspace_slug}/projects/{project_slug}/tasks",
    response_model=TaskOut,
    operation_id="tasks.push",
)
async def push(
    workspace_slug: str,
    project_slug: str,
    body: TaskPushRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TaskOut:
    proj = await get_project_by_slug_path(
        session, principal, workspace_slug=workspace_slug, project_slug=project_slug
    )
    t = await push_task(
        session,
        project_id=proj.id,
        slug=body.slug,
        name=body.name,
        dataset_id=_uuid.UUID(body.dataset_id),
        dataset_version=body.dataset_version,
        scorer_id=_uuid.UUID(body.scorer_id),
        scorer_version=body.scorer_version,
        agent_id=_uuid.UUID(body.agent_id) if body.agent_id else None,
        agent_version=body.agent_version,
        prompt_id=_uuid.UUID(body.prompt_id) if body.prompt_id else None,
        prompt_version=body.prompt_version,
        params_json=body.params_json,
        estimated_cost_usd=body.estimated_cost_usd,
        description=body.description,
    )
    await session.commit()
    return _to_out(t)


@router.get(
    "/workspaces/{workspace_slug}/projects/{project_slug}/tasks",
    response_model=list[TaskOut],
    operation_id="tasks.list",
)
async def list_(
    workspace_slug: str,
    project_slug: str,
    principal: Annotated[Principal, Depends(get_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[TaskOut]:
    proj = await get_project_by_slug_path(
        session, principal, workspace_slug=workspace_slug, project_slug=project_slug
    )
    rows = await list_tasks(session, project_id=proj.id)
    return [_to_out(r) for r in rows]


@router.get(
    "/workspaces/{workspace_slug}/projects/{project_slug}/tasks/{slug}",
    response_model=TaskOut,
    operation_id="tasks.get_latest",
)
async def get_latest(
    workspace_slug: str,
    project_slug: str,
    slug: str,
    principal: Annotated[Principal, Depends(get_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TaskOut:
    proj = await get_project_by_slug_path(
        session, principal, workspace_slug=workspace_slug, project_slug=project_slug
    )
    return _to_out(await get_task_latest(session, project_id=proj.id, slug=slug))
