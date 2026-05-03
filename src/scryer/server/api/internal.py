"""Internal endpoints called only by Railway Cron / health probes.

Not in OpenAPI (include_in_schema=False). Protected by SCRYER_INTERNAL_TOKEN
matching the X-Internal-Token header (set in Railway env + Cron config).

RLS-aware iteration: cron services scan workspace-scoped tables (triggers,
webhook_deliveries, runs — all RLS-policied). Each endpoint iterates active
workspaces (the `workspaces` table itself isn't RLS-policied), sets the
workspace context per workspace via apply_workspace_context, then invokes
the cron service. Service code stays workspace-agnostic; the endpoint
handles tenancy. O(N_workspaces) per cron tick — fine at our scale, and
preserves the no-privileged-bypass invariant.
"""

from __future__ import annotations

import os
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.db import apply_workspace_context, get_session
from scryer.server.models.auth import Workspace
from scryer.server.services.errors import AuthError
from scryer.server.services.runs import reap_stale_runs
from scryer.server.services.triggers import dispatch_due_triggers
from scryer.server.services.webhooks import deliver_pending

router = APIRouter(prefix="/internal", include_in_schema=False)


async def _active_workspace_ids(session: AsyncSession) -> list[uuid.UUID]:
    """Workspaces table isn't RLS-policied, so this listing is allowed
    regardless of current_workspace_id GUC."""
    rows = await session.execute(select(Workspace.id).where(Workspace.archived_at.is_(None)))
    return [r[0] for r in rows.all()]


def _check_internal_token(
    x_internal_token: Annotated[str | None, Header()] = None,
) -> None:
    expected = os.environ.get("SCRYER_INTERNAL_TOKEN")
    if not expected:
        # If no token configured, refuse all internal calls (fail closed).
        raise AuthError("Internal endpoints disabled (SCRYER_INTERNAL_TOKEN not set)")
    if x_internal_token != expected:
        raise AuthError("Invalid X-Internal-Token")


class DispatchOut(BaseModel):
    queued_runs: int


class DeliverOut(BaseModel):
    delivered: int
    failed: int
    dead_letter: int


class ReapOut(BaseModel):
    reaped: int


@router.post("/dispatch-triggers", response_model=DispatchOut, operation_id="internal.dispatch")
async def dispatch(
    _auth: Annotated[None, Depends(_check_internal_token)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DispatchOut:
    total = 0
    for ws_id in await _active_workspace_ids(session):
        await apply_workspace_context(session, ws_id)
        runs = await dispatch_due_triggers(session)
        total += len(runs)
    await session.commit()
    return DispatchOut(queued_runs=total)


@router.post("/deliver-webhooks", response_model=DeliverOut, operation_id="internal.deliver")
async def deliver(
    _auth: Annotated[None, Depends(_check_internal_token)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DeliverOut:
    delivered = failed = dead = 0
    for ws_id in await _active_workspace_ids(session):
        await apply_workspace_context(session, ws_id)
        counts = await deliver_pending(session)
        delivered += counts["delivered"]
        failed += counts["failed"]
        dead += counts["dead_letter"]
    await session.commit()
    return DeliverOut(delivered=delivered, failed=failed, dead_letter=dead)


@router.post("/reap-stale-runs", response_model=ReapOut, operation_id="internal.reap")
async def reap(
    _auth: Annotated[None, Depends(_check_internal_token)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReapOut:
    total = 0
    for ws_id in await _active_workspace_ids(session):
        await apply_workspace_context(session, ws_id)
        total += await reap_stale_runs(session)
    await session.commit()
    return ReapOut(reaped=total)


@router.post("/keepalive", include_in_schema=False)
async def keepalive() -> dict[str, str]:
    """Plan §7: cheap ping to keep Railway from sleeping when there are
    active Runs. No DB call; auth not required."""
    return {"status": "alive"}
