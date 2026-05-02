"""Concurrency stress tests — verify atomic transitions + SKIP LOCKED behavior."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from scryer.server.models.audit import Webhook, WebhookDelivery
from scryer.server.models.enums import (
    MissedFirePolicy,
    TriggerTarget,
    WebhookDeliveryStatus,
)
from scryer.server.services.datasets import push_dataset
from scryer.server.services.errors import ConflictError
from scryer.server.services.projects import create_project
from scryer.server.services.runs import execute_run, queue_run
from scryer.server.services.scorers import push_scorer
from scryer.server.services.tasks import push_task
from scryer.server.services.triggers import (
    create_schedule_trigger,
    dispatch_due_triggers,
)
from scryer.server.services.users import create_user
from scryer.server.services.webhooks import deliver_pending
from scryer.server.services.workspaces import create_workspace


async def _make_session(engine: AsyncEngine) -> AsyncSession:
    return AsyncSession(bind=engine, expire_on_commit=False)


@pytest.mark.slow
async def test_parallel_execute_run_only_one_wins(
    http_session: AsyncSession, engine: AsyncEngine
) -> None:
    """5 parallel execute_run on same run_id → 1 done, 4 ConflictError."""
    sess = http_session
    user = await create_user(sess, email=f"u{uuid4().hex[:6]}@e.com", password="x" * 16)
    ws = await create_workspace(sess, slug=f"w-{uuid4().hex[:6]}", name="t", owner_user_id=user.id)
    proj = await create_project(
        sess, workspace_id=ws.id, slug=f"p-{uuid4().hex[:6]}", name="t", owner_user_id=user.id
    )
    ds = await push_dataset(
        sess, project_id=proj.id, slug="d", name="d", records=[{"inputs": {"x": 1}}]
    )
    src = "def score(inputs, expected, metadata):\n    return {'score': 1.0}\n"
    sc = await push_scorer(sess, project_id=proj.id, slug="s", name="S", source_text=src)
    t = await push_task(
        sess,
        project_id=proj.id,
        slug="t",
        name="T",
        dataset_id=ds.id,
        dataset_version=ds.version,
        scorer_id=sc.id,
        scorer_version=sc.version,
    )
    run = await queue_run(sess, task_id=t.id, workspace_id=ws.id, project_id=proj.id)
    await sess.commit()
    run_id = run.id

    async def worker() -> str:
        s = await _make_session(engine)
        try:
            await execute_run(s, run_id=run_id)
            await s.commit()
            return "ok"
        except ConflictError:
            return "conflict"
        except Exception as exc:
            return f"err:{type(exc).__name__}:{exc}"
        finally:
            await s.close()

    outs = await asyncio.gather(*[worker() for _ in range(5)])
    print(f"\n[execute_run] outcomes: {outs}")
    assert outs.count("ok") == 1, f"expected 1 ok, got {outs}"
    assert outs.count("conflict") == 4, f"expected 4 conflict, got {outs}"


@pytest.mark.slow
async def test_dispatch_triggers_skip_locked(
    http_session: AsyncSession, engine: AsyncEngine
) -> None:
    """Two parallel dispatch_due_triggers should split work, not duplicate."""
    sess = http_session
    user = await create_user(sess, email=f"u{uuid4().hex[:6]}@e.com", password="x" * 16)
    ws = await create_workspace(sess, slug=f"w-{uuid4().hex[:6]}", name="t", owner_user_id=user.id)
    proj = await create_project(
        sess, workspace_id=ws.id, slug=f"p-{uuid4().hex[:6]}", name="t", owner_user_id=user.id
    )
    ds = await push_dataset(
        sess, project_id=proj.id, slug="d", name="d", records=[{"inputs": {"x": 1}}]
    )
    src = "def score(inputs, expected, metadata):\n    return {'score': 1.0}\n"
    sc = await push_scorer(sess, project_id=proj.id, slug="s", name="S", source_text=src)
    t = await push_task(
        sess,
        project_id=proj.id,
        slug="t",
        name="T",
        dataset_id=ds.id,
        dataset_version=ds.version,
        scorer_id=sc.id,
        scorer_version=sc.version,
    )

    trigs = []
    for i in range(6):
        tr = await create_schedule_trigger(
            sess,
            project_id=proj.id,
            workspace_id=ws.id,
            name=f"tr{i}",
            cron_expression="*/10 * * * *",
            target_kind=TriggerTarget.task,
            target_id=t.id,
            missed_fire_policy=MissedFirePolicy.skip_to_latest,
        )
        # Force them due now
        tr.next_fire_at = datetime.now(UTC) - timedelta(minutes=1)
        trigs.append(tr.id)
    await sess.commit()

    async def dispatcher() -> int:
        s = await _make_session(engine)
        try:
            runs = await dispatch_due_triggers(s)
            await s.commit()
            return len(runs)
        finally:
            await s.close()

    a, b = await asyncio.gather(dispatcher(), dispatcher())
    total = a + b
    print(f"\n[dispatch] worker_a={a} worker_b={b} total={total}")
    assert total == 6, f"expected exactly 6 runs queued, got {total} (a={a} b={b})"
    # Verify all triggers fired exactly once (next_fire_at advanced past now)
    s2 = await _make_session(engine)
    try:
        from sqlalchemy import select

        from scryer.server.models.audit import Trigger

        rows = list((await s2.execute(select(Trigger).where(Trigger.id.in_(trigs)))).scalars())
        for r in rows:
            assert r.last_fired_at is not None, f"trigger {r.id} never fired"
    finally:
        await s2.close()


@pytest.mark.slow
async def test_deliver_pending_skip_locked(http_session: AsyncSession, engine: AsyncEngine) -> None:
    """Two parallel deliver_pending should split, not double-deliver."""
    sess = http_session
    user = await create_user(sess, email=f"u{uuid4().hex[:6]}@e.com", password="x" * 16)
    ws = await create_workspace(sess, slug=f"w-{uuid4().hex[:6]}", name="t", owner_user_id=user.id)
    wh = Webhook(
        workspace_id=ws.id,
        name="w",
        url="https://example.com/",
        secret="s" * 32,
        event_types=["x"],
        is_active=True,
    )
    sess.add(wh)
    await sess.flush()
    now = datetime.now(UTC)
    ids = []
    for _ in range(8):
        d = WebhookDelivery(
            webhook_id=wh.id,
            event_type="x",
            payload_json={},
            status=WebhookDeliveryStatus.pending,
            attempts=0,
            next_attempt_at=now - timedelta(seconds=1),
            created_at=now,
        )
        sess.add(d)
        await sess.flush()
        ids.append(d.id)
    await sess.commit()

    async def worker() -> dict[str, int]:
        s = await _make_session(engine)
        try:
            counts = await deliver_pending(s, limit=100)
            await s.commit()
            return counts
        finally:
            await s.close()

    a, b = await asyncio.gather(worker(), worker())
    total_handled = sum(a.values()) + sum(b.values())
    print(f"\n[deliver] a={a} b={b} total_handled={total_handled}")
    assert total_handled == 8, f"expected 8 deliveries handled total, got {total_handled}"
