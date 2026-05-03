"""End-to-end HTTP tests for cluster 2: push dataset/scorer/task → start run → list results."""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.services.projects import create_project
from scryer.server.services.users import create_user
from scryer.server.services.workspaces import create_workspace


async def _signup_with_project(http_session: AsyncSession) -> tuple[str, str, str, str]:
    """Returns (email, password, ws_slug, proj_slug)."""
    email = f"u{uuid4().hex[:8]}@example.com"
    pw = "x" * 16
    user = await create_user(http_session, email=email, password=pw)
    ws = await create_workspace(
        http_session, slug=f"w-{uuid4().hex[:8]}", name="t", owner_user_id=user.id
    )
    proj = await create_project(
        http_session,
        workspace_id=ws.id,
        slug=f"p-{uuid4().hex[:8]}",
        name="t",
        owner_user_id=user.id,
    )
    await http_session.commit()
    return email, pw, ws.slug, proj.slug


async def _login(client: AsyncClient, email: str, pw: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": pw})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.mark.slow
async def test_push_dataset_scorer_task_then_run(
    client: AsyncClient, http_session: AsyncSession
) -> None:
    email, pw, ws, proj = await _signup_with_project(http_session)
    h = await _login(client, email, pw)

    base = f"/api/v1/workspaces/{ws}/projects/{proj}"

    r = await client.post(
        f"{base}/datasets",
        json={
            "slug": "g",
            "name": "Golden",
            "records": [{"inputs": {"x": 1}}, {"inputs": {"x": 2}}],
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    ds = r.json()
    assert ds["version"] == 1

    r = await client.post(
        f"{base}/scorers",
        json={
            "slug": "s",
            "name": "S",
            "source_text": "def score(inputs, expected, metadata): return {'score': inputs['x']}",
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    sc = r.json()

    r = await client.post(
        f"{base}/tasks",
        json={
            "slug": "t",
            "name": "T",
            "dataset_id": ds["id"],
            "dataset_version": ds["version"],
            "scorer_id": sc["id"],
            "scorer_version": sc["version"],
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    task = r.json()

    r = await client.post(f"/api/v1/workspaces/{ws}/runs", json={"task_id": task["id"]}, headers=h)
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "done"
    assert run["n_done"] == 2

    r = await client.get(f"/api/v1/workspaces/{ws}/runs/{run['id']}/results", headers=h)
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 2
    assert {row["score_value"] for row in rows} == {1.0, 2.0}


async def test_dataset_endpoints_require_workspace_membership(
    client: AsyncClient, http_session: AsyncSession
) -> None:
    """User A cannot push datasets to User B's workspace."""
    email_a, pw_a, _, _ = await _signup_with_project(http_session)
    _, _, ws_b, proj_b = await _signup_with_project(http_session)
    h_a = await _login(client, email_a, pw_a)

    r = await client.post(
        f"/api/v1/workspaces/{ws_b}/projects/{proj_b}/datasets",
        json={"slug": "g", "name": "G", "records": [{"inputs": {}}]},
        headers=h_a,
    )
    assert r.status_code == 404
    assert r.json()["type"].endswith("/not-found")
