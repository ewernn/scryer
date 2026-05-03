"""Idempotency-Key middleware tests.

Stripe semantics: silent on missing header, cached replay on retry,
422 on body mismatch, 409 on in-flight, 4xx cached, 5xx not cached.
RLS isolation: cross-tenant key lookups must be invisible.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.services.users import create_user
from scryer.server.services.workspaces import create_workspace


async def _signup(http_session: AsyncSession) -> tuple[str, str, str]:
    email = f"u{uuid4().hex[:8]}@example.com"
    pw = "x" * 16
    user = await create_user(http_session, email=email, password=pw)
    ws = await create_workspace(
        http_session, slug=f"w-{uuid4().hex[:6]}", name="W", owner_user_id=user.id
    )
    await http_session.commit()
    return email, pw, ws.slug


async def _bearer(client: AsyncClient, email: str, pw: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": pw})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _seed_task(http_session: AsyncSession, ws_slug: str):
    """Build the dataset/scorer/task chain that POST /runs needs. Returns
    (workspace_id, task_id) for use in the request body."""
    from sqlalchemy import select

    from scryer.server.models.auth import Workspace
    from scryer.server.services.datasets import push_dataset
    from scryer.server.services.projects import create_project
    from scryer.server.services.scorers import push_scorer
    from scryer.server.services.tasks import push_task

    ws = (
        await http_session.execute(select(Workspace).where(Workspace.slug == ws_slug))
    ).scalar_one()
    proj = await create_project(
        http_session,
        workspace_id=ws.id,
        slug=f"p-{uuid4().hex[:6]}",
        name="P",
        owner_user_id=ws.owner_user_id,
    )
    ds = await push_dataset(
        http_session,
        project_id=proj.id,
        slug="d",
        name="d",
        records=[{"record_id": 1, "inputs": {"x": 1}, "expected": None, "metadata_json": {}}],
    )
    sc = await push_scorer(
        http_session,
        project_id=proj.id,
        slug="s",
        name="s",
        source_text="def score(inputs, expected, metadata):\n    return {'score': 1.0}\n",
    )
    t = await push_task(
        http_session,
        project_id=proj.id,
        slug="t",
        name="t",
        dataset_id=ds.id,
        dataset_version=ds.version,
        scorer_id=sc.id,
        scorer_version=sc.version,
    )
    await http_session.commit()
    return ws.id, t.id


@pytest.mark.slow
async def test_idempotency_replay_returns_cached(
    client: AsyncClient, http_session: AsyncSession
) -> None:
    """Same key + same body → second call returns cached response with
    Idempotent-Replay: true header, without re-executing the run."""
    email, pw, ws = await _signup(http_session)
    h = await _bearer(client, email, pw)
    _, task_id = await _seed_task(http_session, ws)

    body = {"task_id": str(task_id), "execute_now": True}
    key = f"idem-{uuid4().hex[:8]}"
    headers = {**h, "Idempotency-Key": key}

    r1 = await client.post(f"/api/v1/workspaces/{ws}/runs", json=body, headers=headers)
    assert r1.status_code == 200, r1.text
    run_id_1 = r1.json()["id"]

    # Retry with same key + same body — cached row may not yet have committed
    # (BackgroundTasks fire after response sent, race with the second call).
    # The dep's INSERT-ON-CONFLICT will see the placeholder row though, so
    # status_code may be NULL → in-flight 409. That's also a valid outcome.
    r2 = await client.post(f"/api/v1/workspaces/{ws}/runs", json=body, headers=headers)
    assert r2.status_code in (200, 409), r2.text
    if r2.status_code == 200:
        # Cached: same run_id, replay header set
        assert r2.json()["id"] == run_id_1
        assert r2.headers.get("Idempotent-Replay") == "true"


async def test_idempotency_body_mismatch_returns_422(
    client: AsyncClient, http_session: AsyncSession
) -> None:
    """Same key, DIFFERENT body → 422 (key reuse with mismatched request)."""
    email, pw, ws = await _signup(http_session)
    h = await _bearer(client, email, pw)
    _, task_id = await _seed_task(http_session, ws)

    key = f"idem-{uuid4().hex[:8]}"
    body_a = {"task_id": str(task_id), "execute_now": False}
    body_b = {"task_id": str(task_id), "execute_now": True}

    r = await client.post(
        f"/api/v1/workspaces/{ws}/runs", json=body_a, headers={**h, "Idempotency-Key": key}
    )
    assert r.status_code == 200, r.text

    r = await client.post(
        f"/api/v1/workspaces/{ws}/runs", json=body_b, headers={**h, "Idempotency-Key": key}
    )
    # ValidationError → 422 per RFC 9457 mapping
    assert r.status_code == 422, r.text


async def test_idempotency_silent_when_header_absent(
    client: AsyncClient, http_session: AsyncSession
) -> None:
    """No Idempotency-Key header → handler runs normally, no caching."""
    email, pw, ws = await _signup(http_session)
    h = await _bearer(client, email, pw)
    _, task_id = await _seed_task(http_session, ws)

    body = {"task_id": str(task_id), "execute_now": False}
    r1 = await client.post(f"/api/v1/workspaces/{ws}/runs", json=body, headers=h)
    r2 = await client.post(f"/api/v1/workspaces/{ws}/runs", json=body, headers=h)
    assert r1.status_code == 200
    assert r2.status_code == 200
    # Two distinct runs created (no cache)
    assert r1.json()["id"] != r2.json()["id"]


async def test_idempotency_wired_on_dataset_push(
    client: AsyncClient, http_session: AsyncSession
) -> None:
    """POST datasets uses idempotency. Same key+body → second request returns
    cached or in-flight; never duplicates the dataset."""
    from sqlalchemy import select

    from scryer.server.models.auth import Workspace
    from scryer.server.services.projects import create_project

    email, pw, ws_slug = await _signup(http_session)
    h = await _bearer(client, email, pw)
    ws = (
        await http_session.execute(select(Workspace).where(Workspace.slug == ws_slug))
    ).scalar_one()
    proj = await create_project(
        http_session,
        workspace_id=ws.id,
        slug="p",
        name="P",
        owner_user_id=ws.owner_user_id,
    )
    await http_session.commit()

    body = {
        "slug": "ds",
        "name": "DS",
        "records": [{"inputs": {"k": "v"}}],
    }
    key = f"idem-{uuid4().hex[:8]}"
    headers = {**h, "Idempotency-Key": key}

    r1 = await client.post(
        f"/api/v1/workspaces/{ws_slug}/projects/{proj.slug}/datasets",
        json=body,
        headers=headers,
    )
    assert r1.status_code == 200, r1.text
    ds_id_1 = r1.json()["id"]

    r2 = await client.post(
        f"/api/v1/workspaces/{ws_slug}/projects/{proj.slug}/datasets",
        json=body,
        headers=headers,
    )
    assert r2.status_code in (200, 409), r2.text
    if r2.status_code == 200:
        assert r2.json()["id"] == ds_id_1
        assert r2.headers.get("Idempotent-Replay") == "true"


async def test_idempotency_webhook_create_caches_status_only(
    client: AsyncClient, http_session: AsyncSession
) -> None:
    """Webhook create endpoint passes cache_body=False because the response
    includes the one-shot HMAC secret. Replay returns 201 status with NO
    body — the secret cannot be recovered from the cache."""
    email, pw, ws_slug = await _signup(http_session)
    h = await _bearer(client, email, pw)

    body = {
        "name": "wh1",
        "url": "https://example.com/hook",
        "event_types": ["run.completed"],
    }
    key = f"idem-{uuid4().hex[:8]}"
    headers = {**h, "Idempotency-Key": key}

    r1 = await client.post(f"/api/v1/workspaces/{ws_slug}/webhooks", json=body, headers=headers)
    assert r1.status_code == 201, r1.text
    secret_1 = r1.json()["secret"]
    assert secret_1, "first response should expose the secret"

    r2 = await client.post(f"/api/v1/workspaces/{ws_slug}/webhooks", json=body, headers=headers)
    assert r2.status_code in (201, 409), r2.text
    if r2.status_code == 201:
        # Replay path: status preserved, body is null (secret stripped),
        # Idempotent-Replay header present.
        assert r2.headers.get("Idempotent-Replay") == "true"
        # Cached body is NULL — handler returns null/empty content.
        assert r2.text in ("null", "", "{}"), f"replay should return empty body, got: {r2.text!r}"
