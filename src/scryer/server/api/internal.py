"""Internal endpoints called only by Railway Cron / health probes.

Not in OpenAPI (include_in_schema=False). Protected by SCRYER_INTERNAL_TOKEN
matching the X-Internal-Token header (set in Railway env + Cron config).
"""

from __future__ import annotations

import os
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.db import get_session
from scryer.server.services.errors import AuthError
from scryer.server.services.runs import reap_stale_runs
from scryer.server.services.triggers import dispatch_due_triggers
from scryer.server.services.webhooks import deliver_pending

router = APIRouter(prefix="/internal", include_in_schema=False)


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
    runs = await dispatch_due_triggers(session)
    await session.commit()
    return DispatchOut(queued_runs=len(runs))


@router.post("/deliver-webhooks", response_model=DeliverOut, operation_id="internal.deliver")
async def deliver(
    _auth: Annotated[None, Depends(_check_internal_token)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DeliverOut:
    counts = await deliver_pending(session)
    await session.commit()
    return DeliverOut(**counts)


@router.post("/reap-stale-runs", response_model=ReapOut, operation_id="internal.reap")
async def reap(
    _auth: Annotated[None, Depends(_check_internal_token)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReapOut:
    n = await reap_stale_runs(session)
    await session.commit()
    return ReapOut(reaped=n)


@router.post("/keepalive", include_in_schema=False)
async def keepalive() -> dict[str, str]:
    """Plan §7: cheap ping to keep Railway from sleeping when there are
    active Runs. No DB call; auth not required."""
    return {"status": "alive"}
