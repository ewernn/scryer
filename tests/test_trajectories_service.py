"""Trajectory writer: small inline → TrajectoryStep rows; large → R2 spill."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.config import get_settings
from scryer.server.models.enums import TrajectoryStorage
from scryer.server.models.eval import TrajectoryStep
from scryer.server.services.blobs import delete_blob, get_blob
from scryer.server.services.datasets import push_dataset
from scryer.server.services.projects import create_project
from scryer.server.services.runs import queue_run
from scryer.server.services.scorers import push_scorer
from scryer.server.services.tasks import push_task
from scryer.server.services.trajectories import INLINE_THRESHOLD_BYTES, write_trajectory
from scryer.server.services.users import create_user
from scryer.server.services.workspaces import create_workspace
from tests.conftest import workspace_context

pytestmark = pytest.mark.skipif(
    not get_settings().r2_endpoint or not get_settings().r2_access_key_id,
    reason="R2 env vars not configured",
)


async def _setup_run(session: AsyncSession) -> tuple:
    """Returns (run_id, workspace_id, project_id) ready for trajectory writes."""
    user = await create_user(session, email=f"u{uuid4().hex[:8]}@e.com", password="x" * 16)
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
    run = await queue_run(session, task_id=t.id, workspace_id=ws.id, project_id=proj.id)
    return run.id, ws.id, proj.id


async def test_small_trajectory_stays_inline_with_step_rows(session: AsyncSession) -> None:
    run_id, ws, proj = await _setup_run(session)
    steps = [
        {"seq": 0, "kind": "tool_call", "payload": {"x": 1}},
        {"seq": 1, "kind": "result", "payload": {"y": 2}},
    ]
    trajectory = await write_trajectory(
        session,
        run_id=run_id,
        record_id=42,
        workspace_id=ws,
        project_id=proj,
        steps=steps,
    )
    assert trajectory.storage == TrajectoryStorage.inline
    assert trajectory.storage_uri is None
    assert trajectory.n_steps == 2

    async with workspace_context(session, ws):
        rows = list(
            (
                await session.execute(
                    select(TrajectoryStep)
                    .where(TrajectoryStep.trajectory_id == trajectory.id)
                    .order_by(TrajectoryStep.seq)
                )
            ).scalars()
        )
        assert len(rows) == 2
        assert rows[0].kind == "tool_call"
        assert rows[1].payload_json == {"y": 2}


async def test_large_trajectory_spills_to_r2_no_step_rows(session: AsyncSession) -> None:
    """Above INLINE_THRESHOLD_BYTES, the whole trajectory lands in R2 as
    one JSON blob and no TrajectoryStep rows are written to PG."""
    run_id, ws, proj = await _setup_run(session)
    big_payload = {"data": "x" * (INLINE_THRESHOLD_BYTES + 1000)}
    steps = [{"seq": i, "kind": "step", "payload": big_payload} for i in range(3)]

    trajectory = await write_trajectory(
        session,
        run_id=run_id,
        record_id=7,
        workspace_id=ws,
        project_id=proj,
        steps=steps,
    )
    assert trajectory.storage == TrajectoryStorage.r2
    assert trajectory.storage_uri is not None
    assert trajectory.storage_uri.startswith("r2://")
    assert trajectory.storage_sha256 is not None
    assert len(trajectory.storage_sha256) == 64

    # No TrajectoryStep rows for spilled trajectory.
    async with workspace_context(session, ws):
        rows = list(
            (
                await session.execute(
                    select(TrajectoryStep).where(TrajectoryStep.trajectory_id == trajectory.id)
                )
            ).scalars()
        )
        assert rows == []

    # Round-trip the R2 blob to confirm content matches.
    try:
        body = await get_blob(trajectory.storage_uri)
        assert b'"step"' in body
        assert b"xxxx" in body
    finally:
        await delete_blob(trajectory.storage_uri)


async def test_blob_key_is_workspace_scoped(session: AsyncSession) -> None:
    """Spilled blob keys must include ws/{wid}/proj/{pid}/ prefix so a
    misconfigured client can't read another workspace's trajectories."""
    run_id, ws, proj = await _setup_run(session)
    steps = [{"seq": 0, "kind": "step", "payload": {"data": "x" * (INLINE_THRESHOLD_BYTES + 100)}}]
    trajectory = await write_trajectory(
        session,
        run_id=run_id,
        record_id=1,
        workspace_id=ws,
        project_id=proj,
        steps=steps,
    )
    try:
        assert f"ws/{ws}/proj/{proj}/trajectories/" in trajectory.storage_uri
    finally:
        if trajectory.storage_uri:
            await delete_blob(trajectory.storage_uri)
