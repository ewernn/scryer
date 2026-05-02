"""End-to-end: a completed Run fires a webhook event → WebhookDelivery row
exists in the queue. This catches regressions where execute_run forgets to
call fire_event."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.models.audit import WebhookDelivery
from scryer.server.services.datasets import push_dataset
from scryer.server.services.projects import create_project
from scryer.server.services.runs import execute_run, queue_run
from scryer.server.services.scorers import push_scorer
from scryer.server.services.tasks import push_task
from scryer.server.services.users import create_user
from scryer.server.services.webhooks import create_webhook
from scryer.server.services.workspaces import create_workspace


async def test_completed_run_queues_webhook_delivery(session: AsyncSession) -> None:
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

    # Subscribe a webhook to run.done.
    wh = await create_webhook(
        session,
        workspace_id=ws.id,
        name="hook",
        url="https://example.com/hook",
        event_types=["run.done"],
    )

    # Run the Task end-to-end.
    run = await queue_run(session, task_id=t.id, workspace_id=ws.id, project_id=proj.id)
    run = await execute_run(session, run_id=run.id)
    assert run.status.value == "done"

    # fire_event should have queued one delivery for our webhook.
    deliveries = list(
        (
            await session.execute(
                select(WebhookDelivery).where(WebhookDelivery.webhook_id == wh.id)
            )
        ).scalars()
    )
    assert len(deliveries) == 1
    d = deliveries[0]
    assert d.event_type == "run.done"
    assert d.payload_json["run_id"] == str(run.id)
    assert d.payload_json["status"] == "done"
    assert d.payload_json["n_done"] == 1


async def test_webhook_not_subscribed_no_delivery(session: AsyncSession) -> None:
    """A webhook subscribed to a different event type must NOT receive a
    delivery on run.done."""
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
    wh = await create_webhook(
        session,
        workspace_id=ws.id,
        name="hook",
        url="https://example.com/hook",
        event_types=["something.else"],
    )

    run = await queue_run(session, task_id=t.id, workspace_id=ws.id, project_id=proj.id)
    await execute_run(session, run_id=run.id)

    deliveries = list(
        (
            await session.execute(
                select(WebhookDelivery).where(WebhookDelivery.webhook_id == wh.id)
            )
        ).scalars()
    )
    assert deliveries == []
