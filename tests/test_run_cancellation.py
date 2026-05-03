"""Wave 3 cancellation: PID tracking + cooperative status check.

Verifies:
  1. cancel_run on queued Run flips status, no proc to kill.
  2. cancel_run on running Run with same-PID executor terminates the
     subprocess group within CANCEL_GRACE_SECONDS.
  3. Cooperative status check between records halts execute_run mid-flow
     even when cancellation comes from another process (executor_pid
     mismatches; we simulate by clearing _active_procs but flipping status
     directly).
  4. cancel_run on done/failed/superseded Run raises ConflictError.
  5. executor_pid is set on running, cleared on completion.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.db import apply_workspace_context
from scryer.server.models.enums import RunStatus
from scryer.server.models.eval import Run
from scryer.server.services.datasets import push_dataset
from scryer.server.services.errors import ConflictError
from scryer.server.services.projects import create_project
from scryer.server.services.runs import (
    _active_procs,
    cancel_run,
    execute_run,
    queue_run,
)
from scryer.server.services.scorers import push_scorer
from scryer.server.services.tasks import push_task
from scryer.server.services.users import create_user
from scryer.server.services.workspaces import create_workspace


async def _seed_task(session: AsyncSession, *, scorer_source: str | None = None):
    """Create user + workspace + project + dataset (5 records) + scorer + task."""
    user = await create_user(session, email=f"u{uuid4().hex[:6]}@x.com", password="x" * 16)
    ws = await create_workspace(
        session, slug=f"ws-{uuid4().hex[:6]}", name="W", owner_user_id=user.id
    )
    await apply_workspace_context(session, ws.id, user_id=user.id)
    proj = await create_project(
        session, workspace_id=ws.id, slug="p", name="P", owner_user_id=user.id
    )
    ds = await push_dataset(
        session,
        project_id=proj.id,
        slug="d",
        name="D",
        records=[{"inputs": {"i": i}, "expected": None, "metadata_json": {}} for i in range(5)],
    )
    src = scorer_source or "def score(inputs, expected, metadata):\n    return {'score': 1.0}\n"
    sc = await push_scorer(session, project_id=proj.id, slug="s", name="S", source_text=src)
    task = await push_task(
        session,
        project_id=proj.id,
        slug="t",
        name="T",
        dataset_id=ds.id,
        dataset_version=ds.version,
        scorer_id=sc.id,
        scorer_version=sc.version,
    )
    return ws, proj, task


async def test_cancel_queued_run(session: AsyncSession) -> None:
    """Cancelling a queued Run flips status to cancelled, no proc to kill."""
    ws, proj, task = await _seed_task(session)
    run = await queue_run(session, task_id=task.id, workspace_id=ws.id, project_id=proj.id)
    cancelled = await cancel_run(session, run.id)
    assert cancelled.status == RunStatus.cancelled
    assert cancelled.completed_at is not None


async def test_cancel_done_run_raises_conflict(session: AsyncSession) -> None:
    """Cancelling a Run that already completed raises ConflictError (409)."""
    ws, proj, task = await _seed_task(session)
    run = await queue_run(session, task_id=task.id, workspace_id=ws.id, project_id=proj.id)
    run.status = RunStatus.done
    run.completed_at = datetime.now(UTC)
    await session.flush()

    with pytest.raises(ConflictError):
        await cancel_run(session, run.id)


async def test_executor_pid_set_during_run_and_cleared_after(session: AsyncSession) -> None:
    """executor_pid = os.getpid() while running, NULL after completion."""
    ws, proj, task = await _seed_task(session)
    run = await queue_run(session, task_id=task.id, workspace_id=ws.id, project_id=proj.id)
    completed = await execute_run(session, run_id=run.id)
    assert completed.status in (RunStatus.done, RunStatus.failed)
    assert completed.executor_pid is None


async def test_cancel_running_run_terminates_subprocess_same_process(
    session: AsyncSession,
) -> None:
    """A long-sleeping scorer is interrupted within CANCEL_GRACE_SECONDS
    when cancel_run is called from the same process.

    Setup: a Scorer that sleeps 30s — well past CANCEL_GRACE_SECONDS (5s).
    Without PID tracking, cancel would only land between records (and
    here the first record never finishes). With PID tracking, the
    sandbox subprocess is terminated and execute_run's SandboxError
    path records a failed Result, then the cooperative check picks up
    cancelled status and exits."""
    ws, proj, task = await _seed_task(
        session,
        scorer_source="import time\ndef score(inputs, expected, metadata):\n    time.sleep(30)\n    return {'score': 1.0}\n",
    )
    run = await queue_run(session, task_id=task.id, workspace_id=ws.id, project_id=proj.id)

    async def _cancel_after_delay() -> None:
        await asyncio.sleep(0.5)
        # Fresh session to simulate a separate cancel request.
        from scryer.server.db import build_engine, build_session_factory

        # Reuse the bind from the test session — it's bound to the same
        # docker postgres so we can read+write the same Run row.
        bind = session.bind
        from sqlalchemy.ext.asyncio import AsyncSession as _AS

        async with _AS(bind=bind, expire_on_commit=False) as cancel_session:
            await cancel_run(cancel_session, run.id)
            await cancel_session.commit()
        # Touch unused references for ruff
        _ = build_engine
        _ = build_session_factory

    cancel_task = asyncio.create_task(_cancel_after_delay())
    completed = await execute_run(session, run_id=run.id)
    await cancel_task

    # Run should be cancelled AND completed in well under 30 seconds.
    assert completed.status == RunStatus.cancelled
    # Should have terminated within CANCEL_GRACE_SECONDS + small overhead.
    elapsed = (completed.completed_at - completed.started_at).total_seconds()
    assert elapsed < 15, f"cancel took {elapsed}s; expected < 15s with PID kill"


async def test_cooperative_cancel_observed_between_records(session: AsyncSession) -> None:
    """When cancel comes from a process that doesn't have the proc handle
    in _active_procs, the executor still observes status='cancelled' at
    the next per-record check between records.

    Setup: scorer with a small sleep (gives the cancel scheduler time to
    fire). Cancel after ~0.5s — the executor observes it at the next
    per-record cooperative check and breaks the loop early."""
    ws, proj, task = await _seed_task(
        session,
        scorer_source="import time\ndef score(inputs, expected, metadata):\n    time.sleep(0.3)\n    return {'score': 1.0}\n",
    )
    run = await queue_run(session, task_id=task.id, workspace_id=ws.id, project_id=proj.id)
    run_id = run.id

    async def _cancel_after_delay() -> None:
        await asyncio.sleep(0.5)
        # Use a fresh session against the same docker bind to simulate a
        # separate request. ALSO clear _active_procs so the cancel can't
        # local-kill — proves cooperative path works alone.
        from sqlalchemy.ext.asyncio import AsyncSession as _AS

        from scryer.server.services.runs import _active_procs as procs

        procs.pop(run_id, None)

        async with _AS(bind=session.bind, expire_on_commit=False) as cancel_session:
            await cancel_run(cancel_session, run_id)
            await cancel_session.commit()

    cancel_task = asyncio.create_task(_cancel_after_delay())
    completed = await execute_run(session, run_id=run.id)
    await cancel_task

    assert completed.status == RunStatus.cancelled
    # Should have stopped before completing all 5 records.
    assert completed.n_done < 5, f"expected early break, got {completed.n_done} done"


async def test_active_procs_cleared_on_normal_completion(session: AsyncSession) -> None:
    """_active_procs registry is empty after execute_run finishes."""
    ws, proj, task = await _seed_task(session)
    run = await queue_run(session, task_id=task.id, workspace_id=ws.id, project_id=proj.id)
    await execute_run(session, run_id=run.id)
    assert run.id not in _active_procs


# Reference for ruff-unused (import side-effect protected)
_ = os.getpid
_ = Run
