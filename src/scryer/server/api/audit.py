"""AuditEvent endpoint: list events scoped to a workspace."""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.auth import Principal, get_principal
from scryer.server.db import get_session
from scryer.server.services.access import assert_workspace_member, require_workspace_from_path
from scryer.server.services.audit import list_events
from scryer.server.services.workspaces import get_workspace_by_slug

router = APIRouter(
    prefix="/workspaces",
    tags=["audit"],
    dependencies=[Depends(require_workspace_from_path)],
)


class AuditEventOut(BaseModel):
    id: int
    action: str
    actor_kind: str
    actor_user_id: str | None
    actor_service_account_id: str | None
    resource_type: str | None
    resource_id: str | None
    request_id: str | None
    timestamp: datetime
    reason: str | None
    metadata_json: dict[str, Any] | None


@router.get(
    "/{workspace_slug}/audit",
    response_model=list[AuditEventOut],
    operation_id="audit.list",
    summary="List AuditEvents in a workspace (cursor via before_id)",
)
async def list_(
    workspace_slug: str,
    principal: Annotated[Principal, Depends(get_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
    resource_type: str | None = Query(None),
    resource_id: str | None = Query(None),
    actor_user_id: str | None = Query(None),
    request_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    before_id: int | None = Query(None),
) -> list[AuditEventOut]:
    ws = await get_workspace_by_slug(session, workspace_slug)
    await assert_workspace_member(session, principal, ws.id)
    rows = await list_events(
        session,
        workspace_id=ws.id,
        resource_type=resource_type,
        resource_id=_uuid.UUID(resource_id) if resource_id else None,
        actor_user_id=_uuid.UUID(actor_user_id) if actor_user_id else None,
        request_id=request_id,
        limit=limit,
        before_id=before_id,
    )
    return [
        AuditEventOut(
            id=r.id,
            action=r.action,
            actor_kind=r.actor_kind.value,
            actor_user_id=str(r.actor_user_id) if r.actor_user_id else None,
            actor_service_account_id=str(r.actor_service_account_id)
            if r.actor_service_account_id
            else None,
            resource_type=r.resource_type,
            resource_id=str(r.resource_id) if r.resource_id else None,
            request_id=r.request_id,
            timestamp=r.timestamp,
            reason=r.reason,
            metadata_json=r.metadata_json,
        )
        for r in rows
    ]
