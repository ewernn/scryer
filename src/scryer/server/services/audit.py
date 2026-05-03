"""AuditEvent service: append-only log writes + queries.

Per plan §8: this is layer 2 (global audit log). Layer 1 = per-resource
versioning (already in cluster 1+2). Layer 3 = auto-Comments (Phase 4).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.auth import Principal
from scryer.server.db import apply_workspace_context
from scryer.server.models.audit import AuditEvent
from scryer.server.models.enums import ActorKind, PrincipalKind


async def write_event(
    session: AsyncSession,
    *,
    action: str,
    actor: Principal | None = None,
    actor_kind: ActorKind | None = None,
    on_behalf_of_user_id: uuid.UUID | None = None,
    workspace_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: uuid.UUID | None = None,
    before_json: dict[str, Any] | None = None,
    after_json: dict[str, Any] | None = None,
    reason: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    request_id: str | None = None,
    parent_event_id: int | None = None,
    metadata_json: dict[str, Any] | None = None,
) -> AuditEvent:
    """Insert one AuditEvent row. actor inferred from Principal if provided."""
    if actor is not None:
        kind = ActorKind.user if actor.kind == PrincipalKind.user else ActorKind.service_account
        au = actor.id if actor.kind == PrincipalKind.user else None
        sa = actor.id if actor.kind == PrincipalKind.service_account else None
    else:
        kind = actor_kind or ActorKind.system
        au = None
        sa = None

    # audit_events is RLS-policied (USING workspace_id IS NULL OR matches GUC).
    # PG uses the USING clause as the implicit WITH CHECK for INSERT, so any
    # write_event(workspace_id=X) called while the current GUC is Y would
    # raise. Switch context first to defend against caller drift — common
    # case is the signup flow where redeem_invitation has just set GUC to
    # the new personal workspace and the audit row belongs to the inviter
    # workspace.
    if workspace_id is not None:
        await apply_workspace_context(session, workspace_id)

    ev = AuditEvent(
        action=action,
        actor_kind=kind,
        actor_user_id=au,
        actor_service_account_id=sa,
        on_behalf_of_user_id=on_behalf_of_user_id,
        workspace_id=workspace_id,
        project_id=project_id,
        resource_type=resource_type,
        resource_id=resource_id,
        before_json=_redact(before_json),
        after_json=_redact(after_json),
        reason=reason,
        ip_address=ip_address,
        user_agent=user_agent,
        request_id=request_id,
        parent_event_id=parent_event_id,
        timestamp=datetime.now(UTC),
        metadata_json=metadata_json,
    )
    session.add(ev)
    await session.flush()
    return ev


_REDACT_KEYS = frozenset(
    {"password", "password_hash", "encrypted_value", "key_hash", "token_hash", "secret"}
)


def _redact(obj: dict[str, Any] | None) -> dict[str, Any] | None:
    """Strip sensitive fields before persisting to audit_events."""
    if obj is None:
        return None
    out = {}
    for k, v in obj.items():
        if k in _REDACT_KEYS:
            out[k] = "[REDACTED]"
        elif isinstance(v, dict):
            out[k] = _redact(v)  # type: ignore[assignment]
        else:
            out[k] = v
    return out


async def list_events(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    request_id: str | None = None,
    limit: int = 100,
    before_id: int | None = None,
) -> list[AuditEvent]:
    """List events with optional filters; cursor-paginate via before_id."""
    stmt = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)
    if workspace_id is not None:
        stmt = stmt.where(AuditEvent.workspace_id == workspace_id)
    if resource_type is not None:
        stmt = stmt.where(AuditEvent.resource_type == resource_type)
    if resource_id is not None:
        stmt = stmt.where(AuditEvent.resource_id == resource_id)
    if actor_user_id is not None:
        stmt = stmt.where(AuditEvent.actor_user_id == actor_user_id)
    if request_id is not None:
        stmt = stmt.where(AuditEvent.request_id == request_id)
    if before_id is not None:
        stmt = stmt.where(AuditEvent.id < before_id)
    return list((await session.execute(stmt)).scalars())


async def get_event(session: AsyncSession, event_id: int) -> AuditEvent | None:
    return await session.get(AuditEvent, event_id)
