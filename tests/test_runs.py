"""End-to-end Run executor tests: real subprocess sandbox + Result persistence."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.models.enums import RunStatus
from scryer.server.services.datasets import push_dataset
from scryer.server.services.errors import ConflictError
from scryer.server.services.projects import create_project
from scryer.server.services.runs import (
    cancel_run,
    execute_run,
    list_results,
    queue_run,
)
from scryer.server.services.sandbox import SandboxError, run_user_code
from scryer.server.services.scorers import push_scorer
from scryer.server.services.tasks import push_task
from scryer.server.services.users import create_user
from scryer.server.services.workspaces import create_workspace


async def _setup_run(session: AsyncSession, *, scorer_src: str):
    user = await create_user(session, email=f"u{uuid4().hex[:6]}@e.com", password="x" * 16)
    ws = await create_workspace(
        session, slug=f"w-{uuid4().hex[:6]}", name="t", owner_user_id=user.id
    )
    proj = await create_project(
        session,
        workspace_id=ws.id,
        slug=f"p-{uuid4().hex[:6]}",
        name="t",
        owner_user_id=user.id,
    )
    ds = await push_dataset(
        session,
        project_id=proj.id,
        slug="d",
        name="d",
        records=[{"inputs": {"x": 1}}, {"inputs": {"x": 2}}, {"inputs": {"x": 3}}],
    )
    sc = await push_scorer(session, project_id=proj.id, slug="s", name="S", source_text=scorer_src)
    t = await push_task(
        session,
        project_id=proj.id,
        slug="t",
        name="T",
        dataset_id=ds.id,
        dataset_version=ds.version,
        scorer_id=sc.id,
        scorer_version=sc.version,
    )
    return ws, proj, t


@pytest.mark.slow
async def test_run_executor_happy_path(session: AsyncSession) -> None:
    """Trivial Scorer that returns a fixed score."""
    src = "def score(inputs, expected, metadata):\n    return {'score': float(inputs['x'])}\n"
    ws, proj, t = await _setup_run(session, scorer_src=src)
    run = await queue_run(session, task_id=t.id, workspace_id=ws.id, project_id=proj.id)
    assert run.status == RunStatus.queued

    finished = await execute_run(session, run_id=run.id)
    assert finished.status == RunStatus.done
    assert finished.n_done == 3
    assert finished.n_failed == 0

    results = await list_results(session, run.id)
    assert {r.record_id for r in results} == {1, 2, 3}
    assert {float(r.score_value or 0) for r in results} == {1.0, 2.0, 3.0}


@pytest.mark.slow
async def test_run_with_failing_scorer(session: AsyncSession) -> None:
    """Scorer that always raises — Run completes; Results carry error."""
    src = "def score(inputs, expected, metadata):\n    raise ValueError('boom')\n"
    ws, proj, t = await _setup_run(session, scorer_src=src)
    run = await queue_run(session, task_id=t.id, workspace_id=ws.id, project_id=proj.id)
    finished = await execute_run(session, run_id=run.id)
    assert finished.status == RunStatus.failed
    assert finished.n_failed == 3
    results = await list_results(session, run.id)
    assert all(r.error and "boom" in r.error for r in results)


@pytest.mark.slow
async def test_cancel_run_blocks_execute(session: AsyncSession) -> None:
    src = "def score(inputs, expected, metadata):\n    return {'score': 0}\n"
    ws, proj, t = await _setup_run(session, scorer_src=src)
    run = await queue_run(session, task_id=t.id, workspace_id=ws.id, project_id=proj.id)
    cancelled = await cancel_run(session, run.id)
    assert cancelled.status == RunStatus.cancelled
    with pytest.raises(ConflictError):
        await execute_run(session, run_id=run.id)


@pytest.mark.slow
async def test_sandbox_returns_sandbox_error_on_bad_source() -> None:
    with pytest.raises(SandboxError):
        await run_user_code(source="raise SystemExit(1)\n", entry="missing", payload={})


@pytest.mark.slow
async def test_sandbox_timeout() -> None:
    with pytest.raises(SandboxError, match="timeout"):
        await run_user_code(
            source="import time\ndef sleep(): time.sleep(10)\n",
            entry="sleep",
            payload={},
            timeout_s=2,
        )


@pytest.mark.slow
async def test_sandbox_stdout_pollution_doesnt_break_envelope() -> None:
    """Regression: critic flagged that user prints could corrupt the JSON
    envelope. Envelope now goes to a side-channel file."""
    src = (
        "def f(x):\n"
        "    print('user noise stdout')\n"
        "    print('more noise')\n"
        "    return {'score': x}\n"
    )
    result = await run_user_code(source=src, entry="f", payload={"x": 0.42})
    assert result.output == {"score": 0.42}


@pytest.mark.slow
async def test_sandbox_signature_mismatch_helpful_error() -> None:
    """Scorer with wrong signature should fail with a clear message."""
    src = "def score(only_inputs): return 1\n"
    with pytest.raises(SandboxError, match="signature mismatch"):
        await run_user_code(
            source=src,
            entry="score",
            payload={"inputs": {}, "expected": None, "metadata": None},
        )


@pytest.mark.slow
async def test_double_execute_run_second_call_raises_conflict(session: AsyncSession) -> None:
    """Atomic queued→running prevents double execution. Test the sequential
    case (true concurrency requires multiple connections; covered in prod by
    the WHERE status='queued' guard)."""
    src = "def score(inputs, expected, metadata): return {'score': 1.0}\n"
    ws, proj, t = await _setup_run(session, scorer_src=src)
    run = await queue_run(session, task_id=t.id, workspace_id=ws.id, project_id=proj.id)

    finished = await execute_run(session, run_id=run.id)
    assert finished.status == RunStatus.done

    with pytest.raises(ConflictError):
        await execute_run(session, run_id=run.id)
