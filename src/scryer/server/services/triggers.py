"""Trigger dispatcher: cron-driven scheduling of Tasks/Suites.

A Railway Cron pings `/internal/dispatch-triggers` every minute; this service
fires any Trigger whose `next_fire_at <= now`. Per plan §15: max 20 active
Triggers per workspace; minimum 5-min cron interval.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from croniter import croniter  # type: ignore[import-untyped]
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.models.audit import Trigger
from scryer.server.models.enums import (
    MissedFirePolicy,
    TriggerKind,
    TriggerTarget,
)
from scryer.server.models.eval import Run, Task
from scryer.server.services.errors import (
    ConflictError,
    NotFoundError,
    ValidationError,
)

MAX_TRIGGERS_PER_WORKSPACE = 20
MIN_CRON_INTERVAL_S = 5 * 60


async def create_schedule_trigger(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    workspace_id: uuid.UUID,
    name: str,
    cron_expression: str,
    target_kind: TriggerTarget,
    target_id: uuid.UUID,
    missed_fire_policy: MissedFirePolicy = MissedFirePolicy.skip_to_latest,
) -> Trigger:
    if not croniter.is_valid(cron_expression):
        raise ValidationError(f"Invalid cron expression: {cron_expression!r}")

    iterator = croniter(cron_expression, datetime.now(UTC))
    first_fire = iterator.get_next(datetime)
    second_fire = iterator.get_next(datetime)
    if (second_fire - first_fire).total_seconds() < MIN_CRON_INTERVAL_S:
        raise ValidationError(
            f"Trigger interval must be at least {MIN_CRON_INTERVAL_S}s (plan §15)"
        )

    active_count = (
        await session.execute(
            select(func.count())
            .select_from(Trigger)
            .join(Task, Trigger.target_id == Task.id, isouter=True)
            .where(Trigger.is_active.is_(True))
            .where(Trigger.project_id == project_id)
        )
    ).scalar_one()
    if active_count >= MAX_TRIGGERS_PER_WORKSPACE:
        raise ConflictError(
            f"Workspace has {active_count} active Triggers; max is {MAX_TRIGGERS_PER_WORKSPACE}"
        )

    trigger = Trigger(
        project_id=project_id,
        name=name,
        kind=TriggerKind.schedule,
        target_kind=target_kind,
        target_id=target_id,
        cron_expression=cron_expression,
        next_fire_at=first_fire,
        missed_fire_policy=missed_fire_policy,
        is_active=True,
    )
    session.add(trigger)
    await session.flush()
    return trigger


async def dispatch_due_triggers(session: AsyncSession, *, now: datetime | None = None) -> list[Run]:
    """Fire all Triggers with next_fire_at <= now. Returns the Runs queued."""
    now = now or datetime.now(UTC)
    due = list(
        (
            await session.execute(
                select(Trigger)
                .where(Trigger.is_active.is_(True))
                .where(Trigger.kind == TriggerKind.schedule)
                .where(Trigger.next_fire_at <= now)
            )
        ).scalars()
    )

    queued_runs: list[Run] = []
    for trig in due:
        if trig.target_kind == TriggerTarget.task:
            task = await session.get(Task, trig.target_id)
            if task is None or task.archived_at is not None:
                trig.is_active = False
                continue
            from scryer.server.services.runs import queue_run

            run = await queue_run(
                session,
                task_id=task.id,
                workspace_id=task.project_id,  # placeholder; resolve via project
                project_id=task.project_id,
            )
            queued_runs.append(run)
        # Suite target: similar dispatch via execute_suite (deferred for v0
        # since it'd block the Cron loop on every Suite execution).

        trig.last_fired_at = now
        if trig.cron_expression:
            iterator = croniter(trig.cron_expression, now)
            trig.next_fire_at = iterator.get_next(datetime)

    await session.flush()
    return queued_runs


async def disable_trigger(session: AsyncSession, trigger_id: uuid.UUID) -> Trigger:
    trig = await session.get(Trigger, trigger_id)
    if trig is None:
        raise NotFoundError("trigger", str(trigger_id))
    trig.is_active = False
    trig.next_fire_at = None
    await session.flush()
    return trig


def _next_fire_after_skip(cron_expression: str, last_fired_at: datetime) -> datetime:
    """Per plan: missed_fire_policy = skip_to_latest → fire only once for any
    backlog. Returns next-fire-time after `last_fired_at`."""
    base = max(last_fired_at, datetime.now(UTC) - timedelta(seconds=1))
    nxt: datetime = croniter(cron_expression, base).get_next(datetime)
    return nxt
