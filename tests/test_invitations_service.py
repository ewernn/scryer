"""Unit tests for invitations service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.models.auth import Project, Workspace, WorkspaceMember
from scryer.server.models.enums import InvitationStatus, WorkspaceRole
from scryer.server.services.errors import AuthError
from scryer.server.services.invitations import (
    create_invitation,
    redeem_invitation,
    revoke_invitation,
)
from scryer.server.services.users import create_user
from scryer.server.services.workspaces import create_workspace


async def _user(session: AsyncSession):
    return await create_user(session, email=f"u{uuid4().hex[:8]}@example.com", password="x" * 16)


async def _ws(session: AsyncSession, owner_id):
    return await create_workspace(
        session, slug=f"ws-{uuid4().hex[:8]}", name="W", owner_user_id=owner_id
    )


async def test_create_invitation_happy_path(session: AsyncSession) -> None:
    owner = await _user(session)
    ws = await _ws(session, owner.id)
    inv, token = await create_invitation(
        session,
        workspace_id=ws.id,
        workspace_role=WorkspaceRole.member,
        invited_by=owner.id,
        email=f"new{uuid4().hex[:8]}@example.com",
    )
    assert token.startswith("scrinv_")
    assert inv.workspace_id == ws.id
    assert inv.status == InvitationStatus.pending


async def test_redeem_invitation_creates_user_membership_personal_ws_default_project(
    session: AsyncSession,
) -> None:
    owner = await _user(session)
    ws = await _ws(session, owner.id)
    new_email = f"new{uuid4().hex[:8]}@example.com"
    _, token = await create_invitation(
        session,
        workspace_id=ws.id,
        workspace_role=WorkspaceRole.member,
        invited_by=owner.id,
        email=new_email,
    )
    user, inv = await redeem_invitation(
        session, full_token=token, email=new_email, password="redeem-pw-123-xx"
    )
    assert user.email == new_email
    assert inv.used_by_user_id == user.id

    members = await session.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == ws.id, WorkspaceMember.user_id == user.id
        )
    )
    assert members.scalar_one_or_none() is not None

    personal = await session.execute(
        select(Workspace).where(Workspace.owner_user_id == user.id, Workspace.id != ws.id)
    )
    personal_ws = personal.scalar_one()
    default_proj = await session.execute(
        select(Project).where(Project.workspace_id == personal_ws.id, Project.slug == "default")
    )
    assert default_proj.scalar_one() is not None


async def test_redeem_invitation_email_mismatch_raises(session: AsyncSession) -> None:
    owner = await _user(session)
    ws = await _ws(session, owner.id)
    _, token = await create_invitation(
        session,
        workspace_id=ws.id,
        workspace_role=WorkspaceRole.member,
        invited_by=owner.id,
        email=f"target{uuid4().hex[:8]}@example.com",
    )
    with pytest.raises(AuthError):
        await redeem_invitation(
            session,
            full_token=token,
            email=f"other{uuid4().hex[:8]}@example.com",
            password="x" * 16,
        )


async def test_redeem_invitation_revoked_raises(session: AsyncSession) -> None:
    owner = await _user(session)
    ws = await _ws(session, owner.id)
    inv, token = await create_invitation(
        session,
        workspace_id=ws.id,
        workspace_role=WorkspaceRole.member,
        invited_by=owner.id,
    )
    await revoke_invitation(session, inv.id)
    with pytest.raises(AuthError):
        await redeem_invitation(
            session,
            full_token=token,
            email=f"x{uuid4().hex[:8]}@example.com",
            password="x" * 16,
        )


async def test_redeem_invitation_expired_raises(session: AsyncSession) -> None:
    owner = await _user(session)
    ws = await _ws(session, owner.id)
    inv, token = await create_invitation(
        session,
        workspace_id=ws.id,
        workspace_role=WorkspaceRole.member,
        invited_by=owner.id,
    )
    # Shift created_at into the past so expires_at can also be in the past
    # while still satisfying the CHECK (expires_at > created_at).
    now = datetime.now(UTC)
    inv.created_at = now - timedelta(days=2)
    inv.expires_at = now - timedelta(days=1)
    await session.flush()
    with pytest.raises(AuthError):
        await redeem_invitation(
            session,
            full_token=token,
            email=f"x{uuid4().hex[:8]}@example.com",
            password="x" * 16,
        )


async def test_revoke_invitation_flow(session: AsyncSession) -> None:
    """Single revoke succeeds; double-revoke raises ConflictError (fail-fast,
    not silent no-op)."""
    from scryer.server.services.errors import ConflictError

    owner = await _user(session)
    ws = await _ws(session, owner.id)
    inv, _ = await create_invitation(
        session,
        workspace_id=ws.id,
        workspace_role=WorkspaceRole.member,
        invited_by=owner.id,
    )
    revoked = await revoke_invitation(session, inv.id)
    assert revoked.revoked_at is not None
    assert revoked.status == InvitationStatus.revoked
    with pytest.raises(ConflictError):
        await revoke_invitation(session, inv.id)
