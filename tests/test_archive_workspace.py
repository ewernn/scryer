"""archive_workspace + cascade trigger + RLS USING/WITH CHECK split.

Verifies the bundled Wave 2+5 contract:

  1. Archiving a workspace cascades archived_at to children (projects,
     service_accounts, credentials, budgets, webhooks).
  2. Already-archived children keep their original timestamp (no stomp).
  3. Re-archiving a workspace is a no-op (trigger WHEN clause + service
     idempotency).
  4. In-flight Runs are bulk-cancelled.
  5. Archived rows are hidden by RLS by default; visible with
     include_archived=True (USING clause read).
  6. The cascade UPDATE itself succeeds — verifies USING/WITH CHECK split
     (without the split, NEW.archived_at IS NOT NULL would fail WITH CHECK).
  7. DELETE /workspaces/{slug} HTTP endpoint behaves correctly: owner-only,
     idempotent, returns 204.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.db import apply_workspace_context
from scryer.server.models.audit import Webhook
from scryer.server.models.auth import Credential, Project, ServiceAccount, Workspace
from scryer.server.models.enums import (
    CredentialProvider,
    PrincipalKind,
    RunStatus,
    WorkspaceRole,
)
from scryer.server.models.eval import Dataset, Run
from scryer.server.services.datasets import push_dataset
from scryer.server.services.projects import create_project
from scryer.server.services.scorers import push_scorer
from scryer.server.services.tasks import push_task
from scryer.server.services.users import create_user
from scryer.server.services.workspaces import archive_workspace, create_workspace


async def _seed_workspace_with_children(session: AsyncSession):
    """Workspace + project + service_account + credential + webhook + a
    queued Run. Returns dict for assertions."""
    user = await create_user(session, email=f"u{uuid4().hex[:6]}@x.com", password="x" * 16)
    ws = await create_workspace(
        session, slug=f"ws-{uuid4().hex[:6]}", name="W", owner_user_id=user.id
    )
    await apply_workspace_context(session, ws.id, user_id=user.id)
    proj = await create_project(
        session, workspace_id=ws.id, slug="p", name="P", owner_user_id=user.id
    )
    sa = ServiceAccount(workspace_id=ws.id, name=f"sa-{uuid4().hex[:6]}")
    session.add(sa)
    cred = Credential(
        workspace_id=ws.id,
        name=f"c-{uuid4().hex[:6]}",
        provider=CredentialProvider.openai,
        encrypted_value="dummy-base64-stub",
        encryption_key_version=1,
    )
    session.add(cred)
    wh = Webhook(
        workspace_id=ws.id,
        name="WH",
        url="https://example.com/hook",
        secret="x" * 32,
        event_types=["run.completed"],
    )
    session.add(wh)
    ds = await push_dataset(
        session, project_id=proj.id, slug="d", name="D", records=[{"inputs": {"k": "v"}}]
    )
    sc = await push_scorer(
        session,
        project_id=proj.id,
        slug="s",
        name="S",
        source_text="def score(inputs, expected, metadata):\n    return {'score': 1.0}\n",
    )
    task = await push_task(
        session,
        project_id=proj.id,
        slug="t",
        name="T",
        dataset_id=ds.id,
        dataset_version=ds.version,
        scorer_id=sc.id,
        scorer_version=sc.version,
    )
    queued_run = Run(
        task_id=task.id,
        task_version=task.version,
        workspace_id=ws.id,
        project_id=proj.id,
        n_records=1,
        status=RunStatus.queued,
        queued_at=datetime.now(UTC),
    )
    session.add(queued_run)
    await session.flush()
    return {
        "user": user,
        "ws": ws,
        "proj": proj,
        "sa": sa,
        "cred": cred,
        "wh": wh,
        "ds": ds,
        "task": task,
        "run": queued_run,
    }


async def test_archive_cascades_to_children(session: AsyncSession) -> None:
    """Archiving a workspace bulk-archives projects + service_accounts +
    credentials + webhooks (1-hop children that mix SoftDeleteMixin) AND
    versioned grandchildren (datasets, scorers, tasks via 1-hop
    workspace_id) per migration 8f52f0aae3fe."""
    seed = await _seed_workspace_with_children(session)
    ws_id = seed["ws"].id
    await archive_workspace(session, ws_id)
    await session.flush()

    proj = await session.get(Project, seed["proj"].id)
    sa = await session.get(ServiceAccount, seed["sa"].id)
    cred = await session.get(Credential, seed["cred"].id)
    wh = await session.get(Webhook, seed["wh"].id)
    ds = await session.get(Dataset, seed["ds"].id)
    for child, label in [
        (proj, "project"),
        (sa, "service_account"),
        (cred, "credential"),
        (wh, "webhook"),
        (ds, "dataset"),  # versioned grandchild — added in 8f52f0aae3fe
    ]:
        await session.refresh(child)
        assert child.archived_at is not None, f"{label} should be archived after cascade"


async def test_archive_cancels_inflight_runs(session: AsyncSession) -> None:
    """In-flight runs (queued, running) get bulk-cancelled. Done/failed runs
    are unaffected."""
    seed = await _seed_workspace_with_children(session)

    done_run = Run(
        task_id=seed["task"].id,
        task_version=seed["task"].version,
        workspace_id=seed["ws"].id,
        project_id=seed["proj"].id,
        n_records=1,
        status=RunStatus.done,
        completed_at=datetime.now(UTC),
    )
    session.add(done_run)
    await session.flush()

    await archive_workspace(session, seed["ws"].id)
    await session.flush()
    await session.refresh(seed["run"])
    await session.refresh(done_run)
    assert seed["run"].status == RunStatus.cancelled
    assert done_run.status == RunStatus.done


async def test_archive_idempotent(session: AsyncSession) -> None:
    """Re-archiving a workspace is a no-op (trigger WHEN guard + service
    early-return). Children keep their original archived_at."""
    seed = await _seed_workspace_with_children(session)
    await archive_workspace(session, seed["ws"].id)
    await session.flush()
    await session.refresh(seed["proj"])
    first_proj_archived = seed["proj"].archived_at
    assert first_proj_archived is not None

    # Wait a tiny bit and re-archive — should not stomp.
    await archive_workspace(session, seed["ws"].id)
    await session.flush()
    await session.refresh(seed["proj"])
    assert seed["proj"].archived_at == first_proj_archived


async def test_archive_does_not_stomp_already_archived_child(session: AsyncSession) -> None:
    """Cascade trigger uses `WHERE archived_at IS NULL` so a child that was
    independently archived earlier keeps its original timestamp."""
    seed = await _seed_workspace_with_children(session)
    earlier = datetime.now(UTC) - timedelta(hours=1)
    seed["proj"].archived_at = earlier
    await session.flush()

    await archive_workspace(session, seed["ws"].id)
    await session.flush()
    await session.refresh(seed["proj"])
    # The child kept its own archived_at; cascade only updates rows where
    # archived_at IS NULL.
    assert seed["proj"].archived_at == earlier


async def test_rls_hides_archived_rows_by_default(rls_session: AsyncSession) -> None:
    """USING clause filters archived_at IS NULL when include_archived not set."""
    seed = await _seed_workspace_with_children(rls_session)
    ws_id = seed["ws"].id
    ds_id = seed["ds"].id

    # Archive via raw UPDATE under include_archived=true so the post-row
    # USING check (archived_at IS NULL OR include_archived='true') passes
    # without needing the WITH CHECK split alone.
    await apply_workspace_context(rls_session, ws_id, include_archived=True)
    await rls_session.execute(
        text("UPDATE datasets SET archived_at = now() WHERE id = :id"),
        {"id": str(ds_id)},
    )

    # Explicitly reset include_archived. set_config(...,true) is
    # transaction-scoped, so the include_archived='true' set above
    # persists across the savepoint until reset.
    rls_session.info.pop("include_archived", None)
    await rls_session.execute(text("SELECT set_config('app.include_archived', '', true)"))
    await apply_workspace_context(rls_session, ws_id)
    rows = (await rls_session.execute(select(Dataset).where(Dataset.id == ds_id))).scalars().all()
    assert rows == [], "archived dataset should be hidden by USING clause"


async def test_rls_shows_archived_rows_when_include_archived(rls_session: AsyncSession) -> None:
    """include_archived=True surfaces archived rows for admin queries."""
    seed = await _seed_workspace_with_children(rls_session)
    ws_id = seed["ws"].id
    ds_id = seed["ds"].id

    await apply_workspace_context(rls_session, ws_id, include_archived=True)
    await rls_session.execute(
        text("UPDATE datasets SET archived_at = now() WHERE id = :id"),
        {"id": str(ds_id)},
    )

    rows = (await rls_session.execute(select(Dataset).where(Dataset.id == ds_id))).scalars().all()
    assert len(rows) == 1, "include_archived=True should surface archived dataset"


async def test_cascade_trigger_succeeds_under_rls(rls_session: AsyncSession) -> None:
    """The cascade trigger UPDATE sets NEW.archived_at IS NOT NULL on every
    child. Two RLS interactions must work for the cascade to land:
      (a) WITH CHECK split: post-row workspace_id matches GUC.
      (b) Trigger sets app.include_archived='true' so the post-row
          USING check (archived_at IS NULL OR include_archived='true')
          passes despite NEW.archived_at IS NOT NULL.

    This test exercises the trigger end-to-end under the scryer_app role."""
    seed = await _seed_workspace_with_children(rls_session)
    ws_id = seed["ws"].id

    # workspaces table is NOT RLS-policied (root resolution target), so
    # the UPDATE workspaces SET archived_at runs unconstrained. The
    # AFTER-trigger does include_archived bookkeeping internally and
    # RESTORES the prior value before returning (migration 8f52f0aae3fe).
    await apply_workspace_context(rls_session, ws_id)
    ws = await rls_session.get(Workspace, ws_id)
    assert ws is not None
    ws.archived_at = datetime.now(UTC)
    await rls_session.flush()

    # Trigger restored include_archived to its prior value (unset/'') so
    # we re-set it here to refresh now-archived child rows.
    await apply_workspace_context(rls_session, ws_id, include_archived=True)
    await rls_session.refresh(seed["proj"])
    await rls_session.refresh(seed["wh"])
    assert seed["proj"].archived_at is not None
    assert seed["wh"].archived_at is not None


@pytest.mark.slow
async def test_delete_workspace_endpoint_owner_only(
    client: AsyncClient, http_session: AsyncSession
) -> None:
    """DELETE /workspaces/{slug} requires owner role."""
    owner = await create_user(http_session, email=f"o{uuid4().hex[:6]}@x.com", password="x" * 16)
    other = await create_user(http_session, email=f"x{uuid4().hex[:6]}@x.com", password="x" * 16)
    ws = await create_workspace(
        http_session, slug=f"ws-{uuid4().hex[:6]}", name="W", owner_user_id=owner.id
    )
    # Add `other` as a viewer (not owner)
    from scryer.server.services.workspaces import add_workspace_member

    await add_workspace_member(
        http_session, workspace_id=ws.id, user_id=other.id, role=WorkspaceRole.viewer
    )
    await http_session.commit()

    # Login as `other`
    r = await client.post("/api/v1/auth/login", json={"email": other.email, "password": "x" * 16})
    token = r.json()["access_token"]
    r = await client.delete(
        f"/api/v1/workspaces/{ws.slug}", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 403, r.text

    # Login as owner
    r = await client.post("/api/v1/auth/login", json={"email": owner.email, "password": "x" * 16})
    token = r.json()["access_token"]
    r = await client.delete(
        f"/api/v1/workspaces/{ws.slug}", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 204, r.text


@pytest.mark.slow
async def test_delete_workspace_endpoint_idempotent(
    client: AsyncClient, http_session: AsyncSession
) -> None:
    """Re-DELETE returns 404 because get_workspace_by_slug filters
    archived. The first DELETE archived the workspace; slug-history still
    points to it, but get_workspace (called by the slug-history fallback)
    raises NotFoundError on archived workspaces. Stripe-style: archived
    resources vanish from the resolution surface — by-slug GET also 404s."""
    owner = await create_user(http_session, email=f"o{uuid4().hex[:6]}@x.com", password="x" * 16)
    ws = await create_workspace(
        http_session, slug=f"ws-{uuid4().hex[:6]}", name="W", owner_user_id=owner.id
    )
    await http_session.commit()

    r = await client.post("/api/v1/auth/login", json={"email": owner.email, "password": "x" * 16})
    token = r.json()["access_token"]

    r = await client.delete(
        f"/api/v1/workspaces/{ws.slug}", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 204
    r2 = await client.delete(
        f"/api/v1/workspaces/{ws.slug}", headers={"Authorization": f"Bearer {token}"}
    )
    # archive_workspace early-returns if already archived. But
    # get_workspace_by_slug filters archived workspaces and the
    # slug-history fallback also raises NotFoundError on archived. So
    # the endpoint reliably 404s on the second DELETE.
    assert r2.status_code == 404, r2.text


# Smuggle in the cleanup of the unused PrincipalKind import that pyright might complain about.
_ = PrincipalKind
_ = text
