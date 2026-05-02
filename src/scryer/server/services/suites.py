"""Suite service: collect Tasks; trigger SuiteRun aggregating multiple Runs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.models.audit import Suite, SuiteRun, SuiteRunRun, SuiteTask
from scryer.server.models.enums import ActorKind, RunStatus
from scryer.server.models.eval import Run, Task
from scryer.server.services.errors import ConflictError, NotFoundError
from scryer.server.services.runs import execute_run, queue_run
from scryer.server.services.workspaces import validate_slug


async def create_suite(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    slug: str,
    name: str,
    description: str | None = None,
) -> Suite:
    validate_slug(slug)
    if (
        await session.execute(
            select(Suite).where(Suite.project_id == project_id, Suite.slug == slug)
        )
    ).scalar_one_or_none():
        raise ConflictError(f"Suite slug {slug!r} already exists in project")
    s = Suite(project_id=project_id, slug=slug, name=name, description=description)
    session.add(s)
    await session.flush()
    return s


async def add_task_to_suite(
    session: AsyncSession,
    *,
    suite_id: uuid.UUID,
    task_id: uuid.UUID,
    position: int = 0,
    weight: float = 1.0,
) -> SuiteTask:
    if not await session.get(Suite, suite_id):
        raise NotFoundError("suite", str(suite_id))
    if not await session.get(Task, task_id):
        raise NotFoundError("task", str(task_id))
    st = SuiteTask(suite_id=suite_id, task_id=task_id, position=position, weight=weight)
    session.add(st)
    await session.flush()
    return st


async def list_suites(session: AsyncSession, project_id: uuid.UUID) -> list[Suite]:
    return list(
        (
            await session.execute(
                select(Suite)
                .where(Suite.project_id == project_id)
                .order_by(Suite.created_at.desc())
            )
        ).scalars()
    )


async def execute_suite(
    session: AsyncSession,
    *,
    suite_id: uuid.UUID,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    triggered_by_kind: ActorKind = ActorKind.user,
) -> SuiteRun:
    """Queue and execute every Task in the Suite. Per-Task Runs are
    sequential in v0; parallel execution lands in Phase 7+."""
    suite_run = SuiteRun(suite_id=suite_id, triggered_by_kind=triggered_by_kind)
    session.add(suite_run)
    await session.flush()

    tasks = list(
        (
            await session.execute(
                select(SuiteTask).where(SuiteTask.suite_id == suite_id).order_by(SuiteTask.position)
            )
        ).scalars()
    )
    for st in tasks:
        run = await queue_run(
            session, task_id=st.task_id, workspace_id=workspace_id, project_id=project_id
        )
        session.add(SuiteRunRun(suite_run_id=suite_run.id, run_id=run.id))
        await execute_run(session, run_id=run.id)

    suite_run.completed_at = datetime.now(UTC)
    await session.flush()
    return suite_run


async def list_runs_in_suite(session: AsyncSession, suite_run_id: uuid.UUID) -> list[Run]:
    stmt = (
        select(Run)
        .join(SuiteRunRun, SuiteRunRun.run_id == Run.id)
        .where(SuiteRunRun.suite_run_id == suite_run_id)
    )
    return list((await session.execute(stmt)).scalars())


async def aggregate_suite_run_status(
    session: AsyncSession, suite_run_id: uuid.UUID
) -> dict[str, int]:
    """Count of Runs by status for this SuiteRun."""
    runs = await list_runs_in_suite(session, suite_run_id)
    counts: dict[str, int] = {s.value: 0 for s in RunStatus}
    for r in runs:
        counts[r.status.value] += 1
    return counts
