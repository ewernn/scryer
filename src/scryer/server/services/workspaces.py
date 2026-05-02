"""Workspace service: create, list, slug history."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError as SAIntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.models.auth import Workspace, WorkspaceMember, WorkspaceSlug
from scryer.server.models.enums import WorkspaceRole
from scryer.server.services.errors import ConflictError, NotFoundError

_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_RESERVED_SLUGS = frozenset(
    {
        "admin",
        "api",
        "app",
        "www",
        "internal",
        "static",
        "assets",
        "auth",
        "login",
        "logout",
        "signup",
        "billing",
        "settings",
        "system",
        "scryer",
        "public",
        "docs",
    }
)


def validate_slug(slug: str) -> str:
    if not _SLUG_RE.match(slug):
        raise ConflictError(
            f"Invalid slug {slug!r}: lowercase a-z, 0-9, hyphens; 1-64 chars; "
            "cannot start or end with hyphen"
        )
    if slug in _RESERVED_SLUGS:
        raise ConflictError(f"Slug {slug!r} is reserved")
    return slug


async def create_workspace(
    session: AsyncSession,
    *,
    slug: str,
    name: str,
    owner_user_id: uuid.UUID,
) -> Workspace:
    """Create a Workspace. Slug uniqueness checked across the slug history
    table (no reclaim ever). Owner gets an automatic `owner` membership."""
    validate_slug(slug)

    # Reject if slug ever existed (workspace_slugs is the historical record)
    existing = await session.execute(select(WorkspaceSlug).where(WorkspaceSlug.slug == slug))
    if existing.scalar_one_or_none():
        raise ConflictError(f"Workspace slug {slug!r} is already in use or retired")

    ws = Workspace(slug=slug, name=name, owner_user_id=owner_user_id)
    session.add(ws)
    try:
        await session.flush()
    except SAIntegrityError as exc:
        raise ConflictError(f"Workspace slug {slug!r} conflict") from exc

    # Permanent slug record
    session.add(WorkspaceSlug(slug=slug, workspace_id=ws.id))

    # Owner membership
    session.add(
        WorkspaceMember(workspace_id=ws.id, user_id=owner_user_id, role=WorkspaceRole.owner)
    )

    await session.flush()
    return ws


async def get_workspace(session: AsyncSession, ws_id: uuid.UUID) -> Workspace:
    ws = await session.get(Workspace, ws_id)
    if ws is None or ws.archived_at is not None:
        raise NotFoundError("workspace", str(ws_id))
    return ws


async def get_workspace_by_slug(session: AsyncSession, slug: str) -> Workspace:
    """Resolve current workspace by slug; falls back to slug-history
    redirects (returns the workspace whose slug currently aliases via slug
    history). Raises NotFoundError if slug never existed."""
    result = await session.execute(
        select(Workspace).where(Workspace.slug == slug, Workspace.archived_at.is_(None))
    )
    ws = result.scalar_one_or_none()
    if ws is not None:
        return ws
    # Try slug history
    history = await session.execute(select(WorkspaceSlug).where(WorkspaceSlug.slug == slug))
    sh = history.scalar_one_or_none()
    if sh is None:
        raise NotFoundError("workspace", slug)
    return await get_workspace(session, sh.workspace_id)


async def rename_workspace_slug(
    session: AsyncSession, ws_id: uuid.UUID, new_slug: str
) -> Workspace:
    """Rename a workspace's slug. Old slug → permanent redirect via history."""
    validate_slug(new_slug)
    ws = await get_workspace(session, ws_id)

    if ws.slug == new_slug:
        return ws  # no-op

    # New slug must not exist in history
    existing = await session.execute(select(WorkspaceSlug).where(WorkspaceSlug.slug == new_slug))
    if existing.scalar_one_or_none():
        raise ConflictError(f"Slug {new_slug!r} is already in use or retired")

    old_slug = ws.slug
    ws.slug = new_slug
    session.add(WorkspaceSlug(slug=new_slug, workspace_id=ws.id))

    old_row = (
        await session.execute(select(WorkspaceSlug).where(WorkspaceSlug.slug == old_slug))
    ).scalar_one_or_none()
    if old_row is not None:
        old_row.retired_at = datetime.now(UTC)

    await session.flush()
    return ws


async def list_workspaces_for_user(session: AsyncSession, user_id: uuid.UUID) -> list[Workspace]:
    """All non-archived workspaces this user belongs to (any role)."""
    stmt = (
        select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(
            WorkspaceMember.user_id == user_id,
            Workspace.archived_at.is_(None),
        )
        .order_by(Workspace.created_at.desc())
    )
    result = await session.execute(stmt)
    return list(result.scalars())
