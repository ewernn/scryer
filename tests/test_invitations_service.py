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
from tests.conftest import workspace_context


async def _user(session: AsyncSession):
    return await create_user(session, email=f"u{uuid4().hex[:8]}@example.com", password="x" * 16)


async def _ws(session: AsyncSession, owner_id):
    return await create_workspace(
        session, slug=f"ws-{uuid4().hex[:8]}", name="W", owner_user_id=owner_id
    )


async def test_redeem_survives_personal_workspace_slug_collision(
    session: AsyncSession,
) -> None:
    """Regression: previously, a slug collision on personal workspace creation
    triggered `session.rollback()` which wiped the freshly-created User row.
    With nested savepoints, only the failed slug attempt rolls back; the
    parent transaction survives so the User and the eventually-allocated
    workspace both persist.

    Cross-tenant: this test queries Workspace.owner_user_id across multiple
    workspaces (the squatter workspaces + the redeemed personal one). Once
    RLS lands, this select() needs the BYPASSRLS privileged_engine fixture.
    For now we wrap with the inviter ws context (no-op until RLS lands)."""
    owner = await _user(session)
    ws = await _ws(session, owner.id)

    # Pre-occupy the first 2 personal-slug candidates so signup must
    # walk the suffix list and recover from collisions.
    invitee_email = f"collide{uuid4().hex[:8]}@example.com"
    base_slug = invitee_email.split("@", 1)[0].lower().replace("_", "-")
    base = "".join(c if c.isalnum() else "-" for c in base_slug).strip("-") or "user"
    for suffix in ("", "-2"):
        await create_workspace(
            session,
            slug=f"{base}-personal{suffix}"[:64],
            name="squatter",
            owner_user_id=owner.id,
        )

    inv, full_token = await create_invitation(
        session,
        workspace_id=ws.id,
        workspace_role=WorkspaceRole.member,
        invited_by=owner.id,
        email=invitee_email,
    )
    user, _redeemed = await redeem_invitation(
        session,
        full_token=full_token,
        email=invitee_email,
        password="x" * 16,
    )
    # User row must still exist (was getting wiped by the rollback bug).
    assert user.id is not None
    assert user.email == invitee_email
    # Personal workspace must have landed on suffix "-3" (first two were taken).
    # Workspace itself isn't RLS-policied (it's the resolution root); query goes
    # through without GUC. user_id is set for completeness in case anything
    # downstream queries workspace_members.
    async with workspace_context(session, ws.id, user_id=user.id):
        rows = list(
            (
                await session.execute(select(Workspace).where(Workspace.owner_user_id == user.id))
            ).scalars()
        )
        assert any(w.slug.endswith("-personal-3") for w in rows), (
            f"expected -personal-3 suffix, got slugs: {[w.slug for w in rows]}"
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

    # workspace_members policy keys on current_user_id — set it to the new user.
    async with workspace_context(session, ws.id, user_id=user.id):
        members = await session.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == ws.id, WorkspaceMember.user_id == user.id
            )
        )
        assert members.scalar_one_or_none() is not None

    # Workspace itself isn't RLS-policied; this select runs without GUC gating.
    personal = await session.execute(
        select(Workspace).where(Workspace.owner_user_id == user.id, Workspace.id != ws.id)
    )
    personal_ws = personal.scalar_one()
    # projects IS policied; switch the workspace GUC to the new personal workspace.
    async with workspace_context(session, personal_ws.id, user_id=user.id):
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
