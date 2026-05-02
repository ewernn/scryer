"""Run executor: queue, execute (subprocess sandbox + heartbeat), persist Results."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.models.enums import RunStatus
from scryer.server.models.eval import (
    DatasetRecord,
    Result,
    Run,
    Scorer,
    Task,
    Trace,
)
from scryer.server.services.errors import ConflictError, NotFoundError
from scryer.server.services.sandbox import SandboxError, run_user_code

HEARTBEAT_INTERVAL_S = 30


async def queue_run(
    session: AsyncSession,
    *,
    task_id: uuid.UUID,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
) -> Run:
    """Insert a Run row in `queued` state. Caller invokes `execute_run` next."""
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
    await session.flush()
    return run


async def execute_run(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
) -> Run:
    """Synchronous executor: load Task, fetch Records, run Scorer per record,
    persist Results + Trace. Heartbeat every N records.

    Run transitions: queued → running → done | failed.
    """
    run = await session.get(Run, run_id)
    if run is None:
        raise NotFoundError("run", str(run_id))
    if run.status != RunStatus.queued:
        raise ConflictError(f"Run {run_id} is in state {run.status.value}, not queued")

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

    run.status = RunStatus.running
    run.started_at = datetime.now(UTC)
    run.last_heartbeat_at = datetime.now(UTC)
    run.n_records = len(records)
    await session.flush()

    n_done = n_failed = 0
    start_record = (run.resume_cursor or 0) + 1

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
            score_value = _coerce_score(payload)
            session.add(
                Result(
                    run_id=run.id,
                    record_id=rec.record_id,
                    score_value=score_value,
                    score_json=payload,
                    duration_ms=sandbox_result.duration_ms,
                )
            )
            n_done += 1
        except SandboxError as exc:
            session.add(
                Result(
                    run_id=run.id,
                    record_id=rec.record_id,
                    error=str(exc)[:1000],
                )
            )
            n_failed += 1

        run.resume_cursor = rec.record_id
        run.n_done = n_done
        run.n_failed = n_failed

        # Heartbeat every record (small runs); for big ones reduce frequency
        if (n_done + n_failed) % 10 == 0:
            run.last_heartbeat_at = datetime.now(UTC)
            await session.flush()

    run.status = RunStatus.failed if n_failed and not n_done else RunStatus.done
    if n_failed and not n_done:
        run.failure_reason = f"All {n_failed} records failed"
    run.completed_at = datetime.now(UTC)
    run.last_heartbeat_at = run.completed_at

    # Always create one Trace per Run for the whole-run summary;
    # per-record Traces land in Phase 2.5 if/when agents need step-level capture
    session.add(
        Trace(
            run_id=run.id,
            record_id=0,
            n_steps=0,
        )
    )

    await session.flush()
    return run


def _coerce_score(payload: dict[str, Any]) -> float | None:
    """Pull a numeric score out of common Scorer return shapes."""
    if not isinstance(payload, dict):
        return None
    for key in ("score", "value", "result"):
        if key in payload:
            v = payload[key]
            if isinstance(v, (int, float)):
                return float(v)
    return None


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
    stmt = select(Run).where(
        Run.status == RunStatus.running,
        Run.last_heartbeat_at < _now_minus(timeout_seconds),
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
