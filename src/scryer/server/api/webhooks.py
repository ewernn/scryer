"""Webhook CRUD: register HTTP endpoints that scryer POSTs to on lifecycle
events (run.completed, run.failed, etc.).

Workspace-scoped. Owner role required for create/delete/rotate; any member can
list. The webhook `secret` is returned ONLY by create + rotate (one-shot
exposure) — list/get never include it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.auth import Principal, get_principal
from scryer.server.db import get_session
from scryer.server.models.enums import WorkspaceRole
from scryer.server.services.access import assert_workspace_role, require_workspace_from_path
from scryer.server.services.audit import write_event
from scryer.server.services.webhooks import (
    create_webhook,
    delete_webhook,
    list_webhooks,
    rotate_webhook_secret,
)
from scryer.server.services.workspaces import get_workspace_by_slug

router = APIRouter(
    prefix="/workspaces",
    tags=["webhooks"],
    dependencies=[Depends(require_workspace_from_path)],
)


class WebhookOut(BaseModel):
    """Public view — secret intentionally absent."""

    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    url: str
    event_types: list[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime


class WebhookCreatedOut(WebhookOut):
    """Returned ONLY by POST + rotate — includes the secret one time."""

    secret: str


class WebhookCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    url: str = Field(..., min_length=1)
    event_types: list[str] = Field(..., min_length=1)


@router.post(
    "/{workspace_slug}/webhooks",
    response_model=WebhookCreatedOut,
    status_code=status.HTTP_201_CREATED,
    operation_id="webhooks.create",
    summary="Register a webhook (returns secret once)",
)
async def create_(
    workspace_slug: str,
    body: WebhookCreate,
    principal: Annotated[Principal, Depends(get_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WebhookCreatedOut:
    ws = await get_workspace_by_slug(session, workspace_slug)
    await assert_workspace_role(session, principal, ws.id, min_role=WorkspaceRole.owner)
    wh = await create_webhook(
        session,
        workspace_id=ws.id,
        name=body.name,
        url=body.url,
        event_types=body.event_types,
    )
    await write_event(
        session,
        action="webhook.created",
        actor=principal,
        workspace_id=ws.id,
        resource_type="webhook",
        resource_id=wh.id,
        after_json={"name": wh.name, "url": wh.url, "secret": wh.secret},
    )
    await session.commit()
    return WebhookCreatedOut.model_validate(wh)


@router.get(
    "/{workspace_slug}/webhooks",
    response_model=list[WebhookOut],
    operation_id="webhooks.list",
    summary="List webhooks in a workspace",
)
async def list_(
    workspace_slug: str,
    principal: Annotated[Principal, Depends(get_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[WebhookOut]:
    ws = await get_workspace_by_slug(session, workspace_slug)
    await assert_workspace_role(session, principal, ws.id, min_role=WorkspaceRole.viewer)
    rows = await list_webhooks(session, workspace_id=ws.id)
    return [WebhookOut.model_validate(r) for r in rows]


@router.delete(
    "/{workspace_slug}/webhooks/{webhook_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="webhooks.delete",
    summary="Delete a webhook",
)
async def delete_(
    workspace_slug: str,
    webhook_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    ws = await get_workspace_by_slug(session, workspace_slug)
    await assert_workspace_role(session, principal, ws.id, min_role=WorkspaceRole.owner)
    await delete_webhook(session, webhook_id, workspace_id=ws.id)
    await write_event(
        session,
        action="webhook.deleted",
        actor=principal,
        workspace_id=ws.id,
        resource_type="webhook",
        resource_id=webhook_id,
    )
    await session.commit()


@router.post(
    "/{workspace_slug}/webhooks/{webhook_id}/rotate-secret",
    response_model=WebhookCreatedOut,
    operation_id="webhooks.rotate_secret",
    summary="Issue a new HMAC signing secret (returned once)",
)
async def rotate_secret_(
    workspace_slug: str,
    webhook_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WebhookCreatedOut:
    ws = await get_workspace_by_slug(session, workspace_slug)
    await assert_workspace_role(session, principal, ws.id, min_role=WorkspaceRole.owner)
    wh = await rotate_webhook_secret(session, webhook_id, workspace_id=ws.id)
    await write_event(
        session,
        action="webhook.secret_rotated",
        actor=principal,
        workspace_id=ws.id,
        resource_type="webhook",
        resource_id=wh.id,
        after_json={"secret": wh.secret},
    )
    await session.commit()
    return WebhookCreatedOut.model_validate(wh)
