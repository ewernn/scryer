"""Invitation lifecycle endpoints: owner creates an invite token, recipient
trades it for an account via /auth/signup.

Workspace-scoped. Owner role required to create or revoke. Signup is
unauthenticated by design — the invite token IS the proof of authorization."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.auth import Principal, get_principal
from scryer.server.db import get_session
from scryer.server.models.enums import WorkspaceRole
from scryer.server.services.access import assert_workspace_role
from scryer.server.services.audit import write_event
from scryer.server.services.invitations import (
    create_invitation,
    redeem_invitation,
)
from scryer.server.services.security import issue_access_jwt
from scryer.server.services.workspaces import get_workspace_by_slug

invitations_router = APIRouter(prefix="/workspaces", tags=["invitations"])
signup_router = APIRouter(prefix="/auth", tags=["auth"])


class InvitationCreate(BaseModel):
    email: EmailStr | None = None
    workspace_role: WorkspaceRole = WorkspaceRole.member
    ttl_days: int = Field(default=7, ge=1, le=90)


class InvitationCreatedOut(BaseModel):
    """Returned ONLY on POST — includes the raw token one time."""

    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    workspace_id: uuid.UUID
    email: str | None
    workspace_role: WorkspaceRole
    expires_at: datetime
    token: str


class SignupRequest(BaseModel):
    token: str
    email: EmailStr
    password: str = Field(..., min_length=8)
    display_name: str | None = None


class SignupResponse(BaseModel):
    user_id: uuid.UUID
    workspace_id: uuid.UUID
    access_token: str
    expires_in: int


@invitations_router.post(
    "/{workspace_slug}/invitations",
    response_model=InvitationCreatedOut,
    status_code=status.HTTP_201_CREATED,
    operation_id="invitations.create",
    summary="Create an invitation token (returned ONCE)",
)
async def create_(
    workspace_slug: str,
    body: InvitationCreate,
    principal: Annotated[Principal, Depends(get_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> InvitationCreatedOut:
    ws = await get_workspace_by_slug(session, workspace_slug)
    await assert_workspace_role(session, principal, ws.id, min_role=WorkspaceRole.owner)
    inv, full_token = await create_invitation(
        session,
        workspace_id=ws.id,
        workspace_role=body.workspace_role,
        invited_by=principal.id,
        email=body.email,
        ttl_days=body.ttl_days,
    )
    await write_event(
        session,
        action="invitation.created",
        actor=principal,
        workspace_id=ws.id,
        resource_type="invitation",
        resource_id=inv.id,
        after_json={"email": inv.email, "role": inv.workspace_role.value},
    )
    await session.commit()
    return InvitationCreatedOut(
        id=inv.id,
        workspace_id=inv.workspace_id,
        email=inv.email,
        workspace_role=inv.workspace_role,
        expires_at=inv.expires_at,
        token=full_token,
    )


@signup_router.post(
    "/signup",
    response_model=SignupResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="auth.signup",
    summary="Trade an invitation token for a new account + access JWT",
)
async def signup(
    body: SignupRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SignupResponse:
    user, inv = await redeem_invitation(
        session,
        full_token=body.token,
        email=body.email,
        password=body.password,
        display_name=body.display_name,
    )
    await write_event(
        session,
        action="user.signup",
        workspace_id=inv.workspace_id,
        resource_type="user",
        resource_id=user.id,
    )
    await session.commit()
    from scryer.config import get_settings

    s = get_settings()
    return SignupResponse(
        user_id=user.id,
        workspace_id=inv.workspace_id,
        access_token=issue_access_jwt(str(user.id)),
        expires_in=s.jwt_ttl_seconds,
    )
