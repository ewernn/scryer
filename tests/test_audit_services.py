"""Cluster 3 service tests: AuditEvent, Tag, Suite, Usage."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.auth import Principal
from scryer.server.models.enums import ActorKind, ApiScope, PrincipalKind
from scryer.server.services.audit import list_events, write_event
from scryer.server.services.datasets import push_dataset
from scryer.server.services.projects import create_project
from scryer.server.services.scorers import push_scorer
from scryer.server.services.suites import (
    add_task_to_suite,
    create_suite,
    execute_suite,
    list_runs_in_suite,
)
from scryer.server.services.tags import (
    apply_tag,
    create_tag,
    list_tags,
    list_tags_for_resource,
    remove_tag,
)
from scryer.server.services.tasks import push_task
from scryer.server.services.usage import (
    record_usage,
    total_cost_for_workspace_since,
)
from scryer.server.services.users import create_user
from scryer.server.services.workspaces import create_workspace
from tests.conftest import workspace_context


async def _setup(session: AsyncSession):
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
    return user, ws, proj


async def test_audit_event_write_with_principal_user(session: AsyncSession) -> None:
    user, ws, _ = await _setup(session)
    principal = Principal(id=user.id, kind=PrincipalKind.user, scopes=frozenset({ApiScope.read}))
    ev = await write_event(
        session,
        action="resource.create",
        actor=principal,
        workspace_id=ws.id,
        resource_type="workspace",
        resource_id=ws.id,
    )
    assert ev.actor_kind == ActorKind.user
    assert ev.actor_user_id == user.id
    assert ev.actor_service_account_id is None


async def test_audit_event_redacts_sensitive_fields(session: AsyncSession) -> None:
    ev = await write_event(
        session,
        action="resource.update",
        actor_kind=ActorKind.system,
        before_json={"password_hash": "abc", "name": "old"},
        after_json={"password_hash": "xyz", "name": "new"},
    )
    assert ev.before_json["password_hash"] == "[REDACTED]"  # type: ignore[index]
    assert ev.after_json["password_hash"] == "[REDACTED]"  # type: ignore[index]
    assert ev.before_json["name"] == "old"  # type: ignore[index]


async def test_audit_event_list_filters_by_resource(session: AsyncSession) -> None:
    user, ws, _ = await _setup(session)
    rid = uuid4()
    await write_event(
        session,
        action="resource.create",
        actor_kind=ActorKind.system,
        workspace_id=ws.id,
        resource_type="dataset",
        resource_id=rid,
    )
    await write_event(
        session,
        action="resource.update",
        actor_kind=ActorKind.system,
        workspace_id=ws.id,
        resource_type="dataset",
        resource_id=rid,
    )
    async with workspace_context(session, ws.id):
        rows = await list_events(session, resource_type="dataset", resource_id=rid)
        assert len(rows) == 2


async def test_tag_create_and_apply(session: AsyncSession) -> None:
    _, ws, _ = await _setup(session)
    t = await create_tag(session, workspace_id=ws.id, name="urgent")
    rid = uuid4()
    await apply_tag(session, tag_id=t.id, resource_type="run", resource_id=rid)
    async with workspace_context(session, ws.id):
        tags = await list_tags_for_resource(session, resource_type="run", resource_id=rid)
        assert {t.name for t in tags} == {"urgent"}
        await remove_tag(session, tag_id=t.id, resource_type="run", resource_id=rid)
        assert await list_tags_for_resource(session, resource_type="run", resource_id=rid) == []


async def test_tag_list(session: AsyncSession) -> None:
    _, ws, _ = await _setup(session)
    await create_tag(session, workspace_id=ws.id, name="a")
    await create_tag(session, workspace_id=ws.id, name="b")
    async with workspace_context(session, ws.id):
        rows = await list_tags(session, ws.id)
        assert {t.name for t in rows} == {"a", "b"}


async def test_suite_execute_runs_all_tasks(session: AsyncSession) -> None:
    user, ws, proj = await _setup(session)

    ds = await push_dataset(
        session, project_id=proj.id, slug="d", name="d", records=[{"inputs": {"x": 1}}]
    )
    sc = await push_scorer(
        session,
        project_id=proj.id,
        slug="s",
        name="S",
        source_text="def score(inputs, expected, metadata): return {'score': 1.0}",
    )
    t1 = await push_task(
        session,
        project_id=proj.id,
        slug="t1",
        name="T1",
        dataset_id=ds.id,
        dataset_version=ds.version,
        scorer_id=sc.id,
        scorer_version=sc.version,
    )
    t2 = await push_task(
        session,
        project_id=proj.id,
        slug="t2",
        name="T2",
        dataset_id=ds.id,
        dataset_version=ds.version,
        scorer_id=sc.id,
        scorer_version=sc.version,
    )

    async with workspace_context(session, ws.id):
        suite = await create_suite(session, project_id=proj.id, slug="reg", name="Regression")
        await add_task_to_suite(session, suite_id=suite.id, task_id=t1.id, position=1)
        await add_task_to_suite(session, suite_id=suite.id, task_id=t2.id, position=2)

        sr = await execute_suite(session, suite_id=suite.id, workspace_id=ws.id, project_id=proj.id)
        assert sr.completed_at is not None
        runs = await list_runs_in_suite(session, sr.id)
        assert len(runs) == 2
        assert all(r.status.value == "done" for r in runs)


async def test_usage_record_and_aggregate(session: AsyncSession) -> None:
    from datetime import UTC, datetime, timedelta

    _, ws, proj = await _setup(session)
    await record_usage(
        session,
        workspace_id=ws.id,
        project_id=proj.id,
        provider="openai",
        model="gpt-4o-mini",
        input_tokens=100,
        output_tokens=20,
        cost_usd=Decimal("0.0123"),
    )
    await record_usage(
        session,
        workspace_id=ws.id,
        project_id=proj.id,
        provider="openai",
        model="gpt-4o-mini",
        input_tokens=50,
        output_tokens=10,
        cost_usd=Decimal("0.0061"),
    )
    async with workspace_context(session, ws.id):
        total = await total_cost_for_workspace_since(
            session, workspace_id=ws.id, since=datetime.now(UTC) - timedelta(hours=1)
        )
        assert total == Decimal("0.0184")
