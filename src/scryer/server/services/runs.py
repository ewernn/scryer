"""Run executor: queue, execute (subprocess sandbox + heartbeat), persist Results."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.models.enums import RunStatus
from scryer.server.models.eval import (
    DatasetRecord,
    Result,
    Run,
    Scorer,
    Task,
)
from scryer.server.services.errors import ConflictError, NotFoundError
from scryer.server.services.sandbox import SandboxError, run_user_code
from scryer.server.services.scorer_output import parse_scorer_output

HEARTBEAT_INTERVAL_S = 30


async def queue_run(
    session: AsyncSession,
    *,
    task_id: uuid.UUID,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    supersede: bool = False,
) -> Run:
    """Insert a Run row in `queued` state. Caller invokes `execute_run` next.

    If `supersede=True`, any prior queued/running Runs of the same Task are
    atomically marked `superseded` (with completed_at set) BEFORE the new Run
    is inserted. This prevents stale Runs from re-firing when an executor
    later picks up the queue."""
    task = await session.get(Task, task_id)
    if task is None or task.archived_at is not None:
        raise NotFoundError("task", str(task_id))

    run = Run(
        task_id=task.id,
        task_version=task.version,
        workspace_id=workspace_id,
        project_id=project_id,
        status=RunStatus.queued,
        queued_at=datetime.now(UTC),
    )
    session.add(run)
    await session.flush()  # populate run.id

    if supersede:
        # CHECK ck_runs_superseded_coherent enforces:
        # (status='superseded') == (superseded_by_run_id IS NOT NULL).
        # So we must set both in the same UPDATE.
        from sqlalchemy import update

        now = datetime.now(UTC)
        await session.execute(
            update(Run)
            .where(
                Run.task_id == task.id,
                Run.id != run.id,
                Run.status.in_((RunStatus.queued, RunStatus.running)),
            )
            .values(
                status=RunStatus.superseded,
                superseded_by_run_id=run.id,
                completed_at=now,
                failure_reason="superseded",
            )
        )
        await session.flush()
    return run


async def execute_run(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
) -> Run:
    """Atomic queued→running transition prevents duplicate execution.

    Heartbeat by wall-clock (every 30s) so slow Scorers don't get reaped
    by the stale-run sweeper.
    """
    from datetime import timedelta

    from sqlalchemy import update

    now = datetime.now(UTC)
    claim = (
        update(Run)
        .where(Run.id == run_id, Run.status == RunStatus.queued)
        .values(status=RunStatus.running, started_at=now, last_heartbeat_at=now)
        .returning(Run)
    )
    run = (await session.execute(claim)).scalar_one_or_none()
    if run is None:
        existing = await session.get(Run, run_id)
        if existing is None:
            raise NotFoundError("run", str(run_id))
        raise ConflictError(f"Run {run_id} not in queued state ({existing.status.value})")

    task = await session.get(Task, run.task_id)
    assert task is not None
    scorer = await session.get(Scorer, task.scorer_id)
    assert scorer is not None

    records = list(
        (
            await session.execute(
                select(DatasetRecord)
                .where(DatasetRecord.dataset_id == task.dataset_id)
                .order_by(DatasetRecord.record_id)
            )
        ).scalars()
    )
    run.n_records = len(records)
    await session.flush()

    n_done = n_failed = 0
    start_record = (run.resume_cursor or 0) + 1
    last_heartbeat = now

    for rec in records:
        if rec.record_id < start_record:
            continue
        try:
            sandbox_result = await run_user_code(
                source=scorer.source_text,
                entry="score",
                payload={
                    "inputs": rec.inputs,
                    "expected": rec.expected,
                    "metadata": rec.metadata_json,
                },
            )
            payload = sandbox_result.output
            score_value, schema_error = parse_scorer_output(payload)
            session.add(
                Result(
                    run_id=run.id,
                    record_id=rec.record_id,
                    score_value=score_value,
                    score_json=payload,
                    error=schema_error,
                    duration_ms=sandbox_result.duration_ms,
                )
            )
            n_done += 1
        except SandboxError as exc:
            session.add(Result(run_id=run.id, record_id=rec.record_id, error=str(exc)[:1000]))
            n_failed += 1

        run.resume_cursor = rec.record_id
        run.n_done = n_done
        run.n_failed = n_failed

        # Wall-clock heartbeat: every 30s regardless of record count
        now = datetime.now(UTC)
        if (now - last_heartbeat) > timedelta(seconds=HEARTBEAT_INTERVAL_S):
            run.last_heartbeat_at = now
            last_heartbeat = now
            await session.flush()

    run.status = RunStatus.failed if n_failed and not n_done else RunStatus.done
    if n_failed and not n_done:
        run.failure_reason = f"All {n_failed} records failed"
    run.completed_at = datetime.now(UTC)
    run.last_heartbeat_at = run.completed_at
    await session.flush()

    # Plan §8 layer 3: auto-Comment on run.completed/failed
    from scryer.server.services.comments import write_system_comment

    summary = (
        f"Run {run.id} → {run.status.value} ({n_done}/{run.n_records} done, {n_failed} failed)"
    )
    await write_system_comment(
        session,
        project_id=run.project_id,
        body=summary,
        resource_type="run",
        resource_id=run.id,
        structured={
            "tldr": summary,
            "confidence": 1.0,
            "n_done": n_done,
            "n_failed": n_failed,
        },
    )

    # Plan §15: queue webhook deliveries for any subscribers in this workspace.
    # fire_event just inserts WebhookDelivery rows; deliver_pending Cron does
    # the actual HTTP work asynchronously.
    from scryer.server.services.webhooks import fire_event

    await fire_event(
        session,
        workspace_id=run.workspace_id,
        event_type=f"run.{run.status.value}",
        payload={
            "run_id": str(run.id),
            "task_id": str(run.task_id),
            "project_id": str(run.project_id),
            "workspace_id": str(run.workspace_id),
            "status": run.status.value,
            "n_records": run.n_records,
            "n_done": n_done,
            "n_failed": n_failed,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        },
    )
    return run


# _coerce_score replaced by parse_scorer_output (services/scorer_output.py).
# Old behavior silently stored score_value=NULL when Scorer returned a dict
# with the numeric under any key other than score/value/result. New behavior
# returns (score_value, error) — non-conforming returns are recorded as
# explicit errors instead of silent NULLs.


async def get_run(session: AsyncSession, run_id: uuid.UUID) -> Run:
    run = await session.get(Run, run_id)
    if run is None:
        raise NotFoundError("run", str(run_id))
    return run


async def list_results(
    session: AsyncSession, run_id: uuid.UUID, *, limit: int = 1000, offset: int = 0
) -> list[Result]:
    stmt = (
        select(Result)
        .where(Result.run_id == run_id, Result.invalidated_at.is_(None))
        .order_by(Result.record_id)
        .limit(limit)
        .offset(offset)
    )
    return list((await session.execute(stmt)).scalars())


async def cancel_run(session: AsyncSession, run_id: uuid.UUID) -> Run:
    run = await get_run(session, run_id)
    if run.status not in (RunStatus.queued, RunStatus.running):
        raise ConflictError(f"Run {run_id} cannot be cancelled in state {run.status.value}")
    run.status = RunStatus.cancelled
    run.completed_at = datetime.now(UTC)
    await session.flush()
    return run


async def reap_stale_runs(
    session: AsyncSession, *, timeout_seconds: int = 2 * HEARTBEAT_INTERVAL_S
) -> int:
    """Mark Runs `failed` whose heartbeat is older than 2x interval. Called
    by a Cron in production. Returns count reaped."""
    stmt = (
        select(Run)
        .where(
            Run.status == RunStatus.running,
            Run.last_heartbeat_at < _now_minus(timeout_seconds),
        )
        .with_for_update(skip_locked=True)
    )
    rows = list((await session.execute(stmt)).scalars())
    for r in rows:
        r.status = RunStatus.failed
        r.failure_reason = "heartbeat_timeout"
        r.completed_at = datetime.now(UTC)
    await session.flush()
    return len(rows)


def _now_minus(seconds: int) -> datetime:
    from datetime import timedelta

    return datetime.now(UTC) - timedelta(seconds=seconds)
