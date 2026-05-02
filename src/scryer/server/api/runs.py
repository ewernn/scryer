"""Run endpoints: queue + execute, get, list results."""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.auth import Principal, get_principal
from scryer.server.db import get_session
from scryer.server.models.eval import Task
from scryer.server.services.access import assert_project_access
from scryer.server.services.errors import NotFoundError
from scryer.server.services.runs import (
    cancel_run,
    execute_run,
    get_run,
    list_results,
    queue_run,
)

router = APIRouter(tags=["runs"])


class RunStartRequest(BaseModel):
    task_id: str
    execute_now: bool = True  # v0: execute synchronously after queueing
    supersede: bool = False  # mark prior queued/running Runs of this Task as superseded


class RunOut(BaseModel):
    id: str
    task_id: str
    task_version: int
    status: str
    n_records: int
    n_done: int
    n_failed: int
    queued_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    failure_reason: str | None


class ResultOut(BaseModel):
    record_id: int
    score_value: float | None
    score_json: dict[str, Any] | None
    error: str | None
    duration_ms: int | None


def _run_to_out(r: Any) -> RunOut:
    return RunOut(
        id=str(r.id),
        task_id=str(r.task_id),
        task_version=r.task_version,
        status=r.status.value,
        n_records=r.n_records,
        n_done=r.n_done,
        n_failed=r.n_failed,
        queued_at=r.queued_at,
        started_at=r.started_at,
        completed_at=r.completed_at,
        failure_reason=r.failure_reason,
    )


@router.post(
    "/runs",
    response_model=RunOut,
    operation_id="runs.start",
    summary="Queue a Run for a Task; v0 executes synchronously inline",
)
async def start(
    body: RunStartRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RunOut:
    task_uuid = _uuid.UUID(body.task_id)
    task = await session.get(Task, task_uuid)
    if task is None or task.archived_at is not None:
        raise NotFoundError("task", body.task_id)
    proj = await assert_project_access(session, principal, task.project_id)

    run = await queue_run(
        session,
        task_id=task.id,
        workspace_id=proj.workspace_id,
        project_id=proj.id,
        supersede=body.supersede,
    )
    if body.execute_now:
        run = await execute_run(session, run_id=run.id)
    await session.commit()
    return _run_to_out(run)


@router.get(
    "/runs/{run_id}",
    response_model=RunOut,
    operation_id="runs.get",
)
async def get(
    run_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RunOut:
    r = await get_run(session, _uuid.UUID(run_id))
    await assert_project_access(session, principal, r.project_id)
    return _run_to_out(r)


@router.post(
    "/runs/{run_id}/cancel",
    response_model=RunOut,
    operation_id="runs.cancel",
)
async def cancel(
    run_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RunOut:
    r = await get_run(session, _uuid.UUID(run_id))
    await assert_project_access(session, principal, r.project_id)
    r = await cancel_run(session, r.id)
    await session.commit()
    return _run_to_out(r)


@router.get(
    "/runs/{run_id}/results",
    response_model=list[ResultOut],
    operation_id="runs.list_results",
)
async def results(
    run_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(1000, ge=1, le=10000),
    offset: int = Query(0, ge=0),
) -> list[ResultOut]:
    r = await get_run(session, _uuid.UUID(run_id))
    await assert_project_access(session, principal, r.project_id)
    rows = await list_results(session, r.id, limit=limit, offset=offset)
    return [
        ResultOut(
            record_id=row.record_id,
            score_value=float(row.score_value) if row.score_value is not None else None,
            score_json=row.score_json,
            error=row.error,
            duration_ms=row.duration_ms,
        )
        for row in rows
    ]
