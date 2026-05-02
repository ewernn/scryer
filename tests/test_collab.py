"""Cluster 4 tests: Comments + Collections + auto-comment on Run completion."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.models.enums import (
    CollectionPurpose,
    CommentKind,
    PrincipalKind,
    RunStatus,
)
from scryer.server.services.collections import (
    add_member,
    create_collection,
    list_collections,
    list_members,
)
from scryer.server.services.comments import (
    edit_comment,
    list_comments_for_resource,
    write_comment,
    write_system_comment,
)
from scryer.server.services.datasets import push_dataset
from scryer.server.services.errors import PermissionError, ValidationError
from scryer.server.services.projects import create_project
from scryer.server.services.runs import execute_run, queue_run
from scryer.server.services.scorers import push_scorer
from scryer.server.services.tasks import push_task
from scryer.server.services.users import create_user
from scryer.server.services.workspaces import create_workspace


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


async def test_write_comment_user(session: AsyncSession) -> None:
    user, _, proj = await _setup(session)
    c = await write_comment(
        session, project_id=proj.id, body="hi", author_user_id=user.id, kind=CommentKind.user
    )
    assert c.body == "hi"
    assert c.author_user_id == user.id


async def test_write_comment_requires_author(session: AsyncSession) -> None:
    _, _, proj = await _setup(session)
    with pytest.raises(ValidationError):
        await write_comment(session, project_id=proj.id, body="hi", kind=CommentKind.user)


async def test_write_system_comment_no_author(session: AsyncSession) -> None:
    _, _, proj = await _setup(session)
    c = await write_system_comment(session, project_id=proj.id, body="auto")
    assert c.kind == CommentKind.system
    assert c.author_user_id is None


async def test_edit_comment_archives_previous_body(session: AsyncSession) -> None:
    user, _, proj = await _setup(session)
    c = await write_comment(session, project_id=proj.id, body="v1", author_user_id=user.id)
    edited = await edit_comment(
        session,
        comment_id=c.id,
        body="v2",
        editor_user_id=user.id,
        editor_principal_kind=PrincipalKind.user,
    )
    assert edited.body == "v2"
    # version 1 should now exist with body 'v1'
    from sqlalchemy import select

    from scryer.server.models.collab import CommentVersion

    versions = (
        (await session.execute(select(CommentVersion).where(CommentVersion.comment_id == c.id)))
        .scalars()
        .all()
    )
    assert len(versions) == 1
    assert versions[0].body == "v1"


async def test_cannot_edit_system_comment(session: AsyncSession) -> None:
    _, _, proj = await _setup(session)
    c = await write_system_comment(session, project_id=proj.id, body="auto")
    with pytest.raises(PermissionError):
        await edit_comment(session, comment_id=c.id, body="changed")


async def test_list_comments_for_resource(session: AsyncSession) -> None:
    user, _, proj = await _setup(session)
    rid = uuid4()
    await write_comment(
        session,
        project_id=proj.id,
        body="a",
        author_user_id=user.id,
        resource_type="run",
        resource_id=rid,
    )
    await write_comment(
        session,
        project_id=proj.id,
        body="b",
        author_user_id=user.id,
        resource_type="run",
        resource_id=rid,
    )
    rows = await list_comments_for_resource(session, resource_type="run", resource_id=rid)
    assert {r.body for r in rows} == {"a", "b"}


async def test_collection_create_and_add_member(session: AsyncSession) -> None:
    _, _, proj = await _setup(session)
    coll = await create_collection(
        session,
        project_id=proj.id,
        slug="findings",
        name="Findings",
        purpose=CollectionPurpose.investigation,
    )
    assert coll.purpose == CollectionPurpose.investigation
    rid = uuid4()
    await add_member(
        session,
        collection_id=coll.id,
        member_type="run",
        member_id=rid,
        position=1,
        group="failures",
        note="all 100 records failed",
    )
    members = await list_members(session, coll.id)
    assert len(members) == 1
    assert members[0].group == "failures"

    rows = await list_collections(session, proj.id)
    assert len(rows) == 1


@pytest.mark.slow
async def test_run_completion_writes_auto_comment(session: AsyncSession) -> None:
    """Plan §8 layer 3: run.completed → auto Comment with kind='system'."""
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
    run = await queue_run(session, task_id=t.id, workspace_id=ws.id, project_id=proj.id)
    finished = await execute_run(session, run_id=run.id)
    assert finished.status == RunStatus.done

    comments = await list_comments_for_resource(session, resource_type="run", resource_id=run.id)
    assert len(comments) == 1
    assert comments[0].kind == CommentKind.system
    assert "done" in comments[0].body
    assert comments[0].structured is not None
