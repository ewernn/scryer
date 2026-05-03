"""queue_run(supersede=True) marks prior queued/running Runs of the same Task
as `superseded` so stale executors don't re-fire them."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.models.enums import RunStatus
from scryer.server.models.eval import Run
from scryer.server.services.datasets import push_dataset
from scryer.server.services.projects import create_project
from scryer.server.services.runs import queue_run
from scryer.server.services.scorers import push_scorer
from scryer.server.services.tasks import push_task
from scryer.server.services.users import create_user
from scryer.server.services.workspaces import create_workspace
from tests.conftest import workspace_context


async def _setup_task(session: AsyncSession) -> tuple:
    """Returns (task_id, workspace_id, project_id)."""
    user = await create_user(session, email=f"u{uuid4().hex[:8]}@example.com", password="x" * 16)
    ws = await create_workspace(
        session, slug=f"w-{uuid4().hex[:6]}", name="W", owner_user_id=user.id
    )
    proj = await create_project(
        session,
        workspace_id=ws.id,
        slug=f"p-{uuid4().hex[:6]}",
        name="P",
        owner_user_id=user.id,
    )
    ds = await push_dataset(
        session,
        project_id=proj.id,
        slug="d",
        name="d",
        records=[{"record_id": 1, "inputs": {"x": 1}, "expected": None, "metadata_json": {}}],
    )
    sc = await push_scorer(
        session,
        project_id=proj.id,
        slug="s",
        name="s",
        source_text="def score(inputs, expected, metadata):\n    return {'score': 1.0}\n",
    )
    t = await push_task(
        session,
        project_id=proj.id,
        slug="t",
        name="t",
        dataset_id=ds.id,
        dataset_version=ds.version,
        scorer_id=sc.id,
        scorer_version=sc.version,
    )
    return t.id, ws.id, proj.id


async def test_supersede_marks_prior_queued_runs(session: AsyncSession) -> None:
    task_id, ws_id, proj_id = await _setup_task(session)
    r1 = await queue_run(session, task_id=task_id, workspace_id=ws_id, project_id=proj_id)
    r2 = await queue_run(session, task_id=task_id, workspace_id=ws_id, project_id=proj_id)
    assert r1.status == RunStatus.queued
    assert r2.status == RunStatus.queued

    # Third Run with supersede=True marks r1 + r2 as superseded.
    r3 = await queue_run(
        session,
        task_id=task_id,
        workspace_id=ws_id,
        project_id=proj_id,
        supersede=True,
    )
    await session.flush()
    async with workspace_context(session, ws_id):
        rows = list(
            (
                await session.execute(
                    select(Run).where(Run.task_id == task_id).order_by(Run.queued_at)
                )
            ).scalars()
        )
        assert len(rows) == 3
        assert rows[0].status == RunStatus.superseded
        assert rows[0].failure_reason == "superseded"
        assert rows[0].completed_at is not None
        assert rows[1].status == RunStatus.superseded
        assert rows[2].id == r3.id
        assert rows[2].status == RunStatus.queued


async def test_no_supersede_leaves_priors_alone(session: AsyncSession) -> None:
    task_id, ws_id, proj_id = await _setup_task(session)
    r1 = await queue_run(session, task_id=task_id, workspace_id=ws_id, project_id=proj_id)
    r2 = await queue_run(session, task_id=task_id, workspace_id=ws_id, project_id=proj_id)
    assert r1.status == RunStatus.queued
    assert r2.status == RunStatus.queued
