"""Workspace service: create, list, slug history, archive."""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError as SAIntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.models.auth import Workspace, WorkspaceMember, WorkspaceSlug
from scryer.server.models.enums import RunStatus, WorkspaceRole
from scryer.server.models.eval import Run
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
    """Create a Workspace. The workspace_slug_history trigger writes the
    permanent slug record automatically (and rejects re-use of any retired
    slug via the workspace_slugs PK). Owner gets an automatic `owner`
    membership."""
    validate_slug(slug)

    ws = Workspace(slug=slug, name=name, owner_user_id=owner_user_id)
    session.add(ws)
    try:
        await session.flush()
    except SAIntegrityError as exc:
        # Two paths can throw IntegrityError here: the workspaces.slug
        # UNIQUE constraint OR the workspace_slugs PK violation triggered
        # by _trgfn_workspace_slug_history (slug previously retired).
        raise ConflictError(f"Workspace slug {slug!r} is already in use or retired") from exc

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
    """Rename a workspace's slug. The trigger handles the slug-history
    bookkeeping: insert new history row, retire old. PK violation on
    workspace_slugs raises if the new slug was ever in use → propagates
    here as ConflictError."""
    validate_slug(new_slug)
    ws = await get_workspace(session, ws_id)
    if ws.slug == new_slug:
        return ws  # no-op
    ws.slug = new_slug
    try:
        await session.flush()
    except SAIntegrityError as exc:
        raise ConflictError(f"Slug {new_slug!r} is already in use or retired") from exc
    return ws


async def add_workspace_member(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    role: WorkspaceRole,
) -> WorkspaceMember:
    """Add a user to a workspace with the given role. Caller is responsible
    for authorization (only owners may add members in v0). Raises
    ConflictError if the user is already a member."""
    existing = await session.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise ConflictError(f"User {user_id} is already a member of workspace {workspace_id}")
    member = WorkspaceMember(workspace_id=workspace_id, user_id=user_id, role=role)
    session.add(member)
    await session.flush()
    return member


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


async def archive_workspace(session: AsyncSession, workspace_id: uuid.UUID) -> Workspace:
    """Soft-delete a workspace + cascade to children + cancel in-flight runs.

    The DB trigger `trg_workspace_cascade_archive` (migration 9db64f8a534b
    + extension 8f52f0aae3fe) fans archived_at out to projects,
    service_accounts, credentials, budgets, webhooks, datasets, scorers,
    agents, tools, prompts, tasks (1-hop children that mix SoftDeleteMixin
    + carry workspace_id NOT NULL).

    Idempotent: re-archiving an already-archived workspace returns the
    existing row unchanged. The trigger WHEN clause prevents the cascade
    fanout from firing twice.

    In-flight Runs (queued, running) are cancelled in bulk. For Runs whose
    executor lives in THIS process, the subprocess is also terminated
    (Wave 3 PID-tracking). Cross-process Runs land at the next per-record
    cooperative status check.
    """
    from scryer.server.services.runs import CANCEL_GRACE_SECONDS, _active_procs

    ws = await session.get(Workspace, workspace_id)
    if ws is None:
        raise NotFoundError("workspace", str(workspace_id))
    if ws.archived_at is not None:
        # Idempotent — caller treats this as success without re-firing.
        return ws

    # Snapshot in-flight runs in this workspace BEFORE the bulk UPDATE so
    # we can iterate _active_procs without races on the status column.
    inflight = list(
        (
            await session.execute(
                select(Run).where(
                    Run.workspace_id == workspace_id,
                    Run.status.in_([RunStatus.queued, RunStatus.running]),
                )
            )
        ).scalars()
    )
    local_pid = os.getpid()
    procs_to_kill = [
        (r.id, _active_procs.get(r.id))
        for r in inflight
        if r.executor_pid == local_pid and r.id in _active_procs
    ]

    ws.archived_at = datetime.now(UTC)
    await session.flush()  # triggers cascade to children

    await session.execute(
        update(Run)
        .where(
            Run.workspace_id == workspace_id,
            Run.status.in_([RunStatus.queued, RunStatus.running]),
        )
        .values(status=RunStatus.cancelled, completed_at=datetime.now(UTC))
    )

    # Tear down local subprocesses for in-flight Runs in this workspace.
    # Same flow as cancel_run: SIGTERM → grace → SIGKILL. Best-effort —
    # ProcessLookupError is a benign race (proc already exited).
    for _run_id, proc in procs_to_kill:
        if proc is None or proc.returncode is not None:
            continue
        try:
            proc.terminate()
        except (ProcessLookupError, OSError):
            continue
        try:
            await asyncio.wait_for(proc.wait(), timeout=CANCEL_GRACE_SECONDS)
        except TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except (ProcessLookupError, OSError):
                pass

    return ws
