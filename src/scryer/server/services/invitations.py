"""Invitation service: create, redeem (auto-creates Workspace+Project), revoke."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.db import apply_workspace_context
from scryer.server.models.auth import (
    Invitation,
    Project,
    ProjectMember,
    User,
    WorkspaceMember,
)
from scryer.server.models.enums import InvitationStatus, ProjectRole, WorkspaceRole
from scryer.server.services.errors import (
    AuthError,
    ConflictError,
    NotFoundError,
)
from scryer.server.services.projects import create_project
from scryer.server.services.security import (
    generate_invitation_token,
    hash_invitation_token,
)
from scryer.server.services.users import create_user
from scryer.server.services.workspaces import create_workspace

DEFAULT_INVITE_TTL_DAYS = 7


async def create_invitation(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    workspace_role: WorkspaceRole,
    invited_by: uuid.UUID,
    email: str | None = None,
    project_grants: dict[str, Any] | None = None,
    ttl_days: int = DEFAULT_INVITE_TTL_DAYS,
) -> tuple[Invitation, str]:
    """Returns (db row, full_token shown once to inviter)."""
    if project_grants:
        await _validate_project_grants(session, project_grants, workspace_id)

    full_token, token_hash = generate_invitation_token()
    invitation = Invitation(
        token_hash=token_hash,
        email=email,
        workspace_id=workspace_id,
        workspace_role=workspace_role,
        project_grants=project_grants,
        invited_by=invited_by,
        expires_at=datetime.now(UTC) + timedelta(days=ttl_days),
    )
    session.add(invitation)
    await session.flush()
    return invitation, full_token


async def get_invitation_by_token(session: AsyncSession, full_token: str) -> Invitation:
    token_hash = hash_invitation_token(full_token)
    result = await session.execute(select(Invitation).where(Invitation.token_hash == token_hash))
    inv = result.scalar_one_or_none()
    if inv is None:
        raise NotFoundError("invitation", "<token>")
    return inv


async def revoke_invitation(session: AsyncSession, invitation_id: uuid.UUID) -> Invitation:
    inv = await session.get(Invitation, invitation_id)
    if inv is None:
        raise NotFoundError("invitation", str(invitation_id))
    if inv.status not in (InvitationStatus.pending, InvitationStatus.expired):
        raise ConflictError(f"Cannot revoke invitation in state {inv.status.value}")
    if inv.revoked_at is None:
        inv.revoked_at = datetime.now(UTC)
        await session.flush()
    return inv


async def redeem_invitation(
    session: AsyncSession,
    *,
    full_token: str,
    email: str,
    password: str,
    display_name: str | None = None,
) -> tuple[User, Invitation]:
    """Atomic redeem. Two parallel calls to the same token: only one wins.

    Auto-creates personal Workspace + default Project per plan §5.
    """
    token_hash = hash_invitation_token(full_token)
    now = datetime.now(UTC)

    # Atomic claim: only succeeds if invitation is still pending (used_at IS
    # NULL AND revoked_at IS NULL AND expires_at > now). Race-safe.
    claim = (
        update(Invitation)
        .where(
            and_(
                Invitation.token_hash == token_hash,
                Invitation.used_at.is_(None),
                Invitation.revoked_at.is_(None),
                Invitation.expires_at > now,
            )
        )
        .values(used_at=now)
        .returning(Invitation)
    )
    result = await session.execute(claim)
    inv = result.scalar_one_or_none()
    if inv is None:
        raise AuthError("Invitation invalid, used, expired, or revoked")

    if inv.email is not None and inv.email != email:
        raise AuthError("Email does not match invitation")

    user = await create_user(session, email=email, password=password, display_name=display_name)

    session.add(
        WorkspaceMember(workspace_id=inv.workspace_id, user_id=user.id, role=inv.workspace_role)
    )

    # Personal Workspace + default Project; collision retry on slug.
    # Switch RLS context to the new personal workspace BEFORE inserting the
    # default Project (projects table is RLS-policied; without a matching
    # current_workspace_id GUC the INSERT is denied).
    personal_ws = await _create_personal_workspace(session, email, display_name, user.id)
    await apply_workspace_context(session, personal_ws.id, user_id=user.id)
    await create_project(
        session,
        workspace_id=personal_ws.id,
        slug="default",
        name="default",
        owner_user_id=user.id,
    )

    if inv.project_grants:
        # Already validated at create_invitation; double-check for paranoia.
        await _validate_project_grants(session, inv.project_grants, inv.workspace_id)
        for project_id_str, role_str in inv.project_grants.items():
            session.add(
                ProjectMember(
                    project_id=uuid.UUID(project_id_str),
                    user_id=user.id,
                    role=ProjectRole(role_str),
                )
            )

    inv.used_by_user_id = user.id
    await session.flush()
    return user, inv


async def _validate_project_grants(
    session: AsyncSession,
    project_grants: dict[str, Any],
    workspace_id: uuid.UUID,
) -> None:
    """Every project_id in grants must belong to workspace_id and be active.
    Roles must parse. Raises ConflictError on any violation."""
    pids: list[uuid.UUID] = []
    for pid_str, role_str in project_grants.items():
        try:
            pids.append(uuid.UUID(pid_str))
            ProjectRole(role_str)
        except (ValueError, KeyError) as exc:
            raise ConflictError(f"Invalid project_grants entry {pid_str}: {exc}") from exc

    found = await session.execute(
        select(Project.id).where(
            Project.id.in_(pids),
            Project.workspace_id == workspace_id,
            Project.archived_at.is_(None),
        )
    )
    found_ids = {row[0] for row in found.all()}
    missing = set(pids) - found_ids
    if missing:
        raise ConflictError(
            f"project_grants references projects not in workspace {workspace_id}: {missing}"
        )


async def _create_personal_workspace(
    session: AsyncSession,
    email: str,
    display_name: str | None,
    user_id: uuid.UUID,
) -> Any:
    """Create personal workspace; on slug collision append numeric suffix.

    Each attempt runs in a SAVEPOINT (`begin_nested`). A failed attempt rolls
    back ONLY the savepoint, preserving the outer transaction's writes
    (User row, WorkspaceMember from invite redemption, etc.). The previous
    implementation called `session.rollback()` which wiped the entire
    signup transaction on first collision."""
    base = _personal_slug_base(email)
    last_exc: Exception | None = None
    for suffix in ("", "-2", "-3", "-4", "-5"):
        slug = f"{base}{suffix}"[:64]
        try:
            async with session.begin_nested():
                return await create_workspace(
                    session,
                    slug=slug,
                    name=f"{display_name or email}'s workspace",
                    owner_user_id=user_id,
                )
        except ConflictError as exc:
            last_exc = exc
            continue
    raise ConflictError(f"Could not allocate personal workspace slug for {email}") from last_exc


def _personal_slug_base(email: str) -> str:
    local = email.split("@", 1)[0]
    base = "".join(c if c.isalnum() else "-" for c in local.lower()).strip("-") or "user"
    return f"{base}-personal"
