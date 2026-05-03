"""ServiceAccount endpoints: create / list / archive / issue api_key.

All routes nested under /workspaces/{workspace_slug}/service-accounts and
gated by `require_workspace_from_path` at the router level — RLS context
+ workspace membership both established by the dep before the handler runs.

Issuing an SA api_key requires `owner` workspace role: machine credentials
are sensitive enough that a regular member shouldn't be able to mint one
silently."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.auth import Principal, get_principal
from scryer.server.db import get_session
from scryer.server.models.auth import ServiceAccount
from scryer.server.models.enums import ApiScope, PrincipalKind, WorkspaceRole
from scryer.server.services.access import (
    assert_workspace_role,
    require_workspace_from_path,
)
from scryer.server.services.api_keys import issue_api_key
from scryer.server.services.audit import write_event
from scryer.server.services.service_accounts import (
    archive_service_account,
    create_service_account,
    get_service_account,
    list_service_accounts,
)
from scryer.server.services.workspaces import get_workspace_by_slug

router = APIRouter(
    prefix="/workspaces/{workspace_slug}/service-accounts",
    tags=["service_accounts"],
    dependencies=[Depends(require_workspace_from_path)],
)


class ServiceAccountOut(BaseModel):
    id: str
    name: str
    is_active: bool
    requires_approval: bool
    created_at: datetime


class ServiceAccountCreateRequest(BaseModel):
    name: str
    requires_approval: bool = False


class ApiKeyIssueRequest(BaseModel):
    name: str | None = None
    scopes: list[str] = ["read", "write"]


class ApiKeyCreatedOut(BaseModel):
    id: str
    name: str | None
    scopes: list[str]
    full_key: str
    created_at: datetime


def _to_out(sa: ServiceAccount) -> ServiceAccountOut:
    return ServiceAccountOut(
        id=str(sa.id),
        name=sa.name,
        is_active=sa.is_active,
        requires_approval=sa.requires_approval,
        created_at=sa.created_at,
    )


@router.post(
    "",
    response_model=ServiceAccountOut,
    operation_id="service_accounts.create",
    status_code=status.HTTP_201_CREATED,
)
async def create(
    workspace_slug: str,
    body: ServiceAccountCreateRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ServiceAccountOut:
    ws = await get_workspace_by_slug(session, workspace_slug)
    await assert_workspace_role(session, principal, ws.id, min_role=WorkspaceRole.owner)
    owner = principal.id if principal.kind == PrincipalKind.user else None
    sa = await create_service_account(
        session,
        workspace_id=ws.id,
        name=body.name,
        owner_user_id=owner,
        requires_approval=body.requires_approval,
    )
    await write_event(
        session,
        action="service_account.create",
        actor=principal,
        workspace_id=ws.id,
        resource_type="service_account",
        resource_id=sa.id,
        after_json={"name": body.name, "requires_approval": body.requires_approval},
    )
    await session.commit()
    return _to_out(sa)


@router.get(
    "",
    response_model=list[ServiceAccountOut],
    operation_id="service_accounts.list",
)
async def list_(
    workspace_slug: str,
    principal: Annotated[Principal, Depends(get_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[ServiceAccountOut]:
    ws = await get_workspace_by_slug(session, workspace_slug)
    rows = await list_service_accounts(session, ws.id)
    return [_to_out(r) for r in rows]


@router.delete(
    "/{sa_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="service_accounts.archive",
)
async def archive(
    workspace_slug: str,
    sa_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    ws = await get_workspace_by_slug(session, workspace_slug)
    await assert_workspace_role(session, principal, ws.id, min_role=WorkspaceRole.owner)
    sa_uuid = uuid.UUID(sa_id)
    sa = await get_service_account(session, sa_uuid)
    if sa.workspace_id != ws.id:
        # Defense in depth — RLS shouldn't return cross-tenant rows but
        # belt-and-suspenders for service-layer.
        from scryer.server.services.errors import NotFoundError

        raise NotFoundError("service_account", sa_id)
    await archive_service_account(session, sa_uuid)
    await write_event(
        session,
        action="service_account.archive",
        actor=principal,
        workspace_id=ws.id,
        resource_type="service_account",
        resource_id=sa.id,
    )
    await session.commit()


@router.post(
    "/{sa_id}/api-keys",
    response_model=ApiKeyCreatedOut,
    operation_id="service_accounts.issue_api_key",
    status_code=status.HTTP_201_CREATED,
    summary="Issue a new API key for this ServiceAccount; full_key returned ONCE",
)
async def issue_key(
    workspace_slug: str,
    sa_id: str,
    body: ApiKeyIssueRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ApiKeyCreatedOut:
    ws = await get_workspace_by_slug(session, workspace_slug)
    await assert_workspace_role(session, principal, ws.id, min_role=WorkspaceRole.owner)
    sa_uuid = uuid.UUID(sa_id)
    sa = await get_service_account(session, sa_uuid)
    if sa.workspace_id != ws.id:
        from scryer.server.services.errors import NotFoundError

        raise NotFoundError("service_account", sa_id)
    scopes = [ApiScope(s) for s in body.scopes]
    row, full_key = await issue_api_key(
        session,
        principal_kind=PrincipalKind.service_account,
        principal_id=sa.id,
        scopes=scopes,
        name=body.name,
    )
    await write_event(
        session,
        action="service_account.api_key.issue",
        actor=principal,
        workspace_id=ws.id,
        resource_type="api_key",
        resource_id=row.id,
        after_json={"name": body.name, "scopes": body.scopes, "service_account_id": sa_id},
    )
    await session.commit()
    return ApiKeyCreatedOut(
        id=str(row.id),
        name=row.name,
        scopes=[s.value for s in row.scopes],
        full_key=full_key,
        created_at=row.created_at,
    )
