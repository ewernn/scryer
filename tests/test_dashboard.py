"""Phase 5 dashboard smoke tests."""

from __future__ import annotations

from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.services.users import create_user
from scryer.server.services.workspaces import create_workspace


async def _signup(http_session: AsyncSession) -> tuple[str, str, str]:
    email = f"u{uuid4().hex[:6]}@example.com"
    pw = "x" * 16
    user = await create_user(http_session, email=email, password=pw)
    ws = await create_workspace(
        http_session, slug=f"w-{uuid4().hex[:6]}", name="Test", owner_user_id=user.id
    )
    await http_session.commit()
    return email, pw, ws.slug


async def test_login_form_renders(client: AsyncClient) -> None:
    r = await client.get("/web/login")
    assert r.status_code == 200
    assert "Sign in to scryer" in r.text


async def test_workspaces_redirects_when_not_logged_in(client: AsyncClient) -> None:
    r = await client.get("/web/workspaces", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/web/login"


async def test_root_redirects_to_workspaces(client: AsyncClient) -> None:
    r = await client.get("/web", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/web/workspaces"


async def test_login_then_workspaces(client: AsyncClient, http_session: AsyncSession) -> None:
    email, pw, ws_slug = await _signup(http_session)
    r = await client.post(
        "/web/login", data={"email": email, "password": pw}, follow_redirects=False
    )
    assert r.status_code == 303
    assert r.cookies.get("scryer_session")
    cookie = r.cookies["scryer_session"]
    r = await client.get("/web/workspaces", cookies={"scryer_session": cookie})
    assert r.status_code == 200
    assert ws_slug in r.text


async def test_run_page_idor_other_users_run_returns_404(
    client: AsyncClient, http_session: AsyncSession
) -> None:
    """Critic-flagged: run_page must check project access. User A logged in
    cannot read User B's Run by guessing UUID."""
    from uuid import UUID as _U

    from scryer.server.services.datasets import push_dataset
    from scryer.server.services.projects import create_project
    from scryer.server.services.runs import execute_run, queue_run
    from scryer.server.services.scorers import push_scorer
    from scryer.server.services.tasks import push_task

    # User B sets up a Run
    email_b, _pw_b, ws_b = await _signup(http_session)
    user_b_workspace = (
        await http_session.execute(
            __import__("sqlalchemy").select(__import__("scryer.server.models.auth", fromlist=["Workspace"]).Workspace).where(
                __import__("scryer.server.models.auth", fromlist=["Workspace"]).Workspace.slug == ws_b
            )
        )
    ).scalar_one()
    proj_b = await create_project(
        http_session, workspace_id=user_b_workspace.id, slug=f"p-{uuid4().hex[:6]}",
        name="t", owner_user_id=user_b_workspace.owner_user_id,
    )
    ds = await push_dataset(
        http_session, project_id=proj_b.id, slug="d", name="d",
        records=[{"inputs": {"x": 1}}],
    )
    sc = await push_scorer(
        http_session, project_id=proj_b.id, slug="s", name="S",
        source_text="def score(inputs, expected, metadata): return {'score': 1.0}",
    )
    t = await push_task(
        http_session, project_id=proj_b.id, slug="t", name="T",
        dataset_id=ds.id, dataset_version=ds.version,
        scorer_id=sc.id, scorer_version=sc.version,
    )
    run = await queue_run(
        http_session, task_id=t.id, workspace_id=user_b_workspace.id, project_id=proj_b.id
    )
    await execute_run(http_session, run_id=run.id)
    await http_session.commit()

    # User A logs in, tries to read B's Run
    email_a, pw_a, _ = await _signup(http_session)
    r = await client.post(
        "/web/login", data={"email": email_a, "password": pw_a}, follow_redirects=False
    )
    cookie = r.cookies["scryer_session"]
    r = await client.get(
        f"/web/runs/{run.id}",
        cookies={"scryer_session": cookie},
    )
    # Should be 404 not 200; assert_project_access raises NotFoundError → RFC 9457 404
    assert r.status_code == 404, f"IDOR! got {r.status_code}: {r.text[:200]}"
