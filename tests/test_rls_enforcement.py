"""Prove RLS actually enforces visibility on data tables.

Uses `rls_session` (scryer_app role, NOT SUPERUSER, NOT BYPASSRLS) so the
policies installed by migration c4f2e1b9a3d5 actually apply. The default
`session` fixture connects as `postgres` (SUPERUSER) which bypasses RLS
entirely; service-layer tests rely on that to avoid setting workspace
context for every fixture row.

In production, the connecting role is `neondb_owner` (NOT SUPERUSER but
table owner). Neon docs warn that owner bypass is silent unless the
migration uses `ALTER TABLE x FORCE ROW LEVEL SECURITY`. Migration
c4f2e1b9a3d5 does FORCE on every RLS-enabled table, so prod is gated."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.db import apply_workspace_context
from scryer.server.models.eval import Dataset
from scryer.server.services.datasets import push_dataset
from scryer.server.services.projects import create_project
from scryer.server.services.users import create_user
from scryer.server.services.workspaces import create_workspace


async def _seed_two_workspaces(session: AsyncSession):
    """Create two workspaces (each with a project + dataset). Returns
    ((ws_a, proj_a, ds_a), (ws_b, proj_b, ds_b)). Uses apply_workspace_context
    to switch GUC between workspaces because the project/dataset INSERTs
    are RLS-policied."""
    user_a = await create_user(session, email=f"a{uuid4().hex[:6]}@x.com", password="x" * 16)
    user_b = await create_user(session, email=f"b{uuid4().hex[:6]}@x.com", password="x" * 16)
    ws_a = await create_workspace(
        session, slug=f"a-{uuid4().hex[:6]}", name="A", owner_user_id=user_a.id
    )
    ws_b = await create_workspace(
        session, slug=f"b-{uuid4().hex[:6]}", name="B", owner_user_id=user_b.id
    )
    await apply_workspace_context(session, ws_a.id, user_id=user_a.id)
    proj_a = await create_project(
        session, workspace_id=ws_a.id, slug="default", name="P", owner_user_id=user_a.id
    )
    ds_a = await push_dataset(
        session, project_id=proj_a.id, slug="ds", name="DS", records=[{"inputs": {"k": "v"}}]
    )
    await apply_workspace_context(session, ws_b.id, user_id=user_b.id)
    proj_b = await create_project(
        session, workspace_id=ws_b.id, slug="default", name="P", owner_user_id=user_b.id
    )
    ds_b = await push_dataset(
        session, project_id=proj_b.id, slug="ds", name="DS", records=[{"inputs": {"k": "v"}}]
    )
    return (ws_a, proj_a, ds_a), (ws_b, proj_b, ds_b)


async def test_rls_blocks_dataset_select_without_guc(rls_session: AsyncSession) -> None:
    """No GUC set → policy denies → zero rows. Fail-closed."""
    (_, _, ds_a), _ = await _seed_two_workspaces(rls_session)
    # Reset GUC so the next select runs without workspace context.
    await rls_session.execute(text("SELECT set_config('app.current_workspace_id', '', true)"))
    rows = (await rls_session.execute(select(Dataset).where(Dataset.id == ds_a.id))).scalars().all()
    assert rows == [], "RLS should deny when no current_workspace_id GUC is set"


async def test_rls_blocks_cross_tenant_dataset_select(rls_session: AsyncSession) -> None:
    """ws_b context can see ws_b's datasets; CANNOT see ws_a's."""
    (_, _, ds_a), (ws_b, _, ds_b) = await _seed_two_workspaces(rls_session)
    await apply_workspace_context(rls_session, ws_b.id)
    own = (await rls_session.execute(select(Dataset).where(Dataset.id == ds_b.id))).scalars().all()
    foreign = (
        (await rls_session.execute(select(Dataset).where(Dataset.id == ds_a.id))).scalars().all()
    )
    assert len(own) == 1, "ws_b context must see its own dataset"
    assert foreign == [], "ws_b context must NOT see ws_a's dataset"


async def test_rls_blocks_count_query_without_guc(rls_session: AsyncSession) -> None:
    """count(*) is also subject to RLS — fail-closed = zero rows."""
    await _seed_two_workspaces(rls_session)
    await rls_session.execute(text("SELECT set_config('app.current_workspace_id', '', true)"))
    n = (await rls_session.execute(text("SELECT count(*) FROM datasets"))).scalar_one()
    assert n == 0, f"expected 0 rows visible without GUC, got {n}"


async def test_audit_events_no_null_workspace_leak(rls_session: AsyncSession) -> None:
    """Migration e9f1a4c8b3d6 dropped the `workspace_id IS NULL OR matches`
    pass-through. Verify a NULL-workspace audit row (would-be system event)
    is invisible to ANY tenant and that an attempted INSERT with workspace_id
    mismatch fails the WITH CHECK policy.

    This is the regression-protection for the cross-tenant info-disclosure
    fix — without it, a future migration that re-adds `IS NULL OR` would
    silently re-introduce the leak."""
    from sqlalchemy.exc import ProgrammingError

    (ws_a, _, _), _ = await _seed_two_workspaces(rls_session)
    await apply_workspace_context(rls_session, ws_a.id)
    # Try to INSERT an audit row with workspace_id=NULL — must fail
    # WITH CHECK because the policy requires workspace_id = current GUC.
    blocked = False
    try:
        await rls_session.execute(
            text(
                "INSERT INTO audit_events (action, actor_kind, workspace_id, timestamp) "
                "VALUES ('test.system_event', 'system', NULL, now())"
            )
        )
        await rls_session.flush()
    except ProgrammingError as exc:
        blocked = "row-level security policy" in str(exc).lower()
    assert blocked, "RLS must block NULL-workspace audit INSERT under tightened policy"


async def test_rls_blocks_cross_tenant_insert(rls_session: AsyncSession) -> None:
    """INSERT into a table for a different workspace fails the WITH CHECK
    policy. The trigger _trgfn_workspace_from_project also raises on
    explicit mismatch, but here we exercise the policy path: the GUC is
    workspace_a but we INSERT a project explicitly into workspace_b."""
    from sqlalchemy.exc import ProgrammingError

    (ws_a, _, _), (ws_b, _, _) = await _seed_two_workspaces(rls_session)
    await apply_workspace_context(rls_session, ws_a.id)
    # Try to create a project in ws_b while in ws_a context.
    user_x = await create_user(rls_session, email=f"x{uuid4().hex[:6]}@x.com", password="x" * 16)
    raised = False
    try:
        await create_project(
            rls_session, workspace_id=ws_b.id, slug="sneaky", name="X", owner_user_id=user_x.id
        )
    except ProgrammingError as exc:
        raised = "row-level security policy" in str(exc).lower()
    assert raised, "expected RLS to block cross-tenant project INSERT"
