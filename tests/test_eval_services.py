"""Tests for cluster 2 versioned services + Task binding."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.services.agents import push_agent
from scryer.server.services.datasets import (
    count_records,
    get_dataset_latest,
    list_datasets,
    push_dataset,
)
from scryer.server.services.errors import ConflictError, NotFoundError, ValidationError
from scryer.server.services.prompts import push_prompt
from scryer.server.services.scorers import push_scorer
from scryer.server.services.tasks import push_task
from scryer.server.services.tools import push_tool
from scryer.server.services.users import create_user
from scryer.server.services.workspaces import create_workspace

from tests.conftest import workspace_context


async def _project(session: AsyncSession):
    """Returns (project, workspace_id) so tests can wrap reads in workspace_context."""
    from scryer.server.services.projects import create_project

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
    return proj, ws.id


async def test_dataset_push_first_version(session: AsyncSession) -> None:
    proj, ws_id = await _project(session)
    ds = await push_dataset(
        session,
        project_id=proj.id,
        slug="golden",
        name="Golden",
        records=[{"inputs": {"q": "hi"}}, {"inputs": {"q": "bye"}}],
    )
    assert ds.version == 1
    assert ds.parent_id is None
    assert ds.record_count == 2
    async with workspace_context(session, ws_id):
        assert await count_records(session, ds.id) == 2


async def test_dataset_push_second_version_chains(session: AsyncSession) -> None:
    proj, _ws_id = await _project(session)
    v1 = await push_dataset(
        session,
        project_id=proj.id,
        slug="g",
        name="g",
        records=[{"inputs": {"q": "1"}}],
    )
    v2 = await push_dataset(
        session,
        project_id=proj.id,
        slug="g",
        name="g",
        records=[{"inputs": {"q": "1"}}, {"inputs": {"q": "2"}}],
    )
    assert v2.version == 2
    assert v2.parent_id == v1.id


async def test_dataset_get_latest(session: AsyncSession) -> None:
    proj, ws_id = await _project(session)
    await push_dataset(session, project_id=proj.id, slug="g", name="g", records=[{"inputs": {}}])
    v2 = await push_dataset(
        session, project_id=proj.id, slug="g", name="g", records=[{"inputs": {"x": 1}}]
    )
    async with workspace_context(session, ws_id):
        latest = await get_dataset_latest(session, project_id=proj.id, slug="g")
        assert latest.id == v2.id


async def test_dataset_empty_records_rejected(session: AsyncSession) -> None:
    proj, _ws_id = await _project(session)
    with pytest.raises(ConflictError):
        await push_dataset(session, project_id=proj.id, slug="g", name="g", records=[])


async def test_list_datasets_returns_only_latest_per_slug(session: AsyncSession) -> None:
    proj, ws_id = await _project(session)
    await push_dataset(session, project_id=proj.id, slug="a", name="A", records=[{"inputs": {}}])
    await push_dataset(session, project_id=proj.id, slug="a", name="A", records=[{"inputs": {}}])
    await push_dataset(session, project_id=proj.id, slug="b", name="B", records=[{"inputs": {}}])
    async with workspace_context(session, ws_id):
        rows = await list_datasets(session, project_id=proj.id)
        assert {(r.slug, r.version) for r in rows} == {("a", 2), ("b", 1)}


async def test_scorer_push(session: AsyncSession) -> None:
    proj, _ws_id = await _project(session)
    s = await push_scorer(
        session,
        project_id=proj.id,
        slug="s",
        name="S",
        source_text="def score(x): return 1.0",
    )
    assert s.version == 1
    assert s.server_executable is True


async def test_task_binding_validates_versions(session: AsyncSession) -> None:
    proj, _ws_id = await _project(session)
    ds = await push_dataset(
        session, project_id=proj.id, slug="d", name="d", records=[{"inputs": {}}]
    )
    sc = await push_scorer(session, project_id=proj.id, slug="s", name="S", source_text="pass")
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
    assert t.version == 1
    assert t.content_hash != ""


async def test_task_binding_rejects_stale_version(session: AsyncSession) -> None:
    proj, _ws_id = await _project(session)
    ds = await push_dataset(
        session, project_id=proj.id, slug="d", name="d", records=[{"inputs": {}}]
    )
    sc = await push_scorer(session, project_id=proj.id, slug="s", name="S", source_text="pass")
    with pytest.raises(NotFoundError):
        await push_task(
            session,
            project_id=proj.id,
            slug="t",
            name="T",
            dataset_id=ds.id,
            dataset_version=99,
            scorer_id=sc.id,
            scorer_version=sc.version,
        )


async def test_task_agent_id_version_must_agree(session: AsyncSession) -> None:
    proj, _ws_id = await _project(session)
    ds = await push_dataset(
        session, project_id=proj.id, slug="d", name="d", records=[{"inputs": {}}]
    )
    sc = await push_scorer(session, project_id=proj.id, slug="s", name="S", source_text="pass")
    with pytest.raises(ValidationError):
        await push_task(
            session,
            project_id=proj.id,
            slug="t",
            name="T",
            dataset_id=ds.id,
            dataset_version=ds.version,
            scorer_id=sc.id,
            scorer_version=sc.version,
            agent_id=uuid4(),  # without agent_version
        )


async def test_full_binding_with_agent_and_prompt(session: AsyncSession) -> None:
    proj, _ws_id = await _project(session)
    ds = await push_dataset(
        session, project_id=proj.id, slug="d", name="d", records=[{"inputs": {}}]
    )
    sc = await push_scorer(session, project_id=proj.id, slug="s", name="S", source_text="pass")
    ag = await push_agent(session, project_id=proj.id, slug="a", name="A", source_text="pass")
    pr = await push_prompt(session, project_id=proj.id, slug="p", name="P", template="hi")
    t = await push_task(
        session,
        project_id=proj.id,
        slug="t",
        name="T",
        dataset_id=ds.id,
        dataset_version=ds.version,
        scorer_id=sc.id,
        scorer_version=sc.version,
        agent_id=ag.id,
        agent_version=ag.version,
        prompt_id=pr.id,
        prompt_version=pr.version,
    )
    assert t.agent_id == ag.id
    assert t.prompt_id == pr.id


async def test_tool_push_versioning(session: AsyncSession) -> None:
    proj, _ws_id = await _project(session)
    t1 = await push_tool(
        session, project_id=proj.id, slug="t", name="T", source_text="def t(): pass"
    )
    t2 = await push_tool(
        session, project_id=proj.id, slug="t", name="T", source_text="def t(): return 1"
    )
    assert t2.version == 2
    assert t2.parent_id == t1.id
    assert t2.content_hash != t1.content_hash
