"""UsageRecord service: append-only LLM-call ledger.

Called from Scorer / Agent code via a future SDK helper; for v0 the user code
emits via `scryer_usage` env-piped channel (TBD Phase 7 wiring). This service
just exposes the persistence + query primitives."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.models.audit import UsageRecord


async def record_usage(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: Decimal,
    project_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    credential_id: uuid.UUID | None = None,
    latency_ms: int | None = None,
) -> UsageRecord:
    rec = UsageRecord(
        workspace_id=workspace_id,
        project_id=project_id,
        run_id=run_id,
        credential_id=credential_id,
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        timestamp=datetime.now(UTC),
    )
    session.add(rec)
    await session.flush()
    return rec


async def total_cost_for_workspace_since(
    session: AsyncSession, *, workspace_id: uuid.UUID, since: datetime
) -> Decimal:
    stmt = select(func.coalesce(func.sum(UsageRecord.cost_usd), 0)).where(
        UsageRecord.workspace_id == workspace_id,
        UsageRecord.timestamp >= since,
    )
    result = (await session.execute(stmt)).scalar_one()
    return Decimal(str(result))
