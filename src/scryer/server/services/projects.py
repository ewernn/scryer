"""Project service: create, list, slug history; visibility checks."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.models.auth import (
    Project,
    ProjectMember,
    ProjectSlug,
    WorkspaceMember,
)
from scryer.server.models.enums import ProjectRole, ProjectVisibility
from scryer.server.services.errors import ConflictError, NotFoundError
from scryer.server.services.workspaces import validate_slug


async def create_project(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    slug: str,
    name: str,
    owner_user_id: uuid.UUID,
    visibility: ProjectVisibility = ProjectVisibility.workspace,
) -> Project:
    """Create a Project. The project_slug_history trigger writes the
    permanent slug record and rejects re-use of any retired slug via the
    project_slugs (workspace_id, slug) UNIQUE."""
    validate_slug(slug)

    proj = Project(workspace_id=workspace_id, slug=slug, name=name, visibility=visibility)
    session.add(proj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ConflictError(
            f"Project slug {slug!r} is already in use or retired in this workspace"
        ) from exc

    session.add(ProjectMember(project_id=proj.id, user_id=owner_user_id, role=ProjectRole.owner))
    await session.flush()
    return proj


async def get_project(session: AsyncSession, project_id: uuid.UUID) -> Project:
    proj = await session.get(Project, project_id)
    if proj is None or proj.archived_at is not None:
        raise NotFoundError("project", str(project_id))
    return proj


async def get_project_by_slug(session: AsyncSession, workspace_id: uuid.UUID, slug: str) -> Project:
    result = await session.execute(
        select(Project).where(
            Project.workspace_id == workspace_id,
            Project.slug == slug,
            Project.archived_at.is_(None),
        )
    )
    proj = result.scalar_one_or_none()
    if proj is not None:
        return proj
    history = await session.execute(
        select(ProjectSlug).where(
            ProjectSlug.workspace_id == workspace_id, ProjectSlug.slug == slug
        )
    )
    sh = history.scalar_one_or_none()
    if sh is None:
        raise NotFoundError("project", f"{workspace_id}/{slug}")
    return await get_project(session, sh.project_id)


async def list_projects_for_user_in_workspace(
    session: AsyncSession, *, workspace_id: uuid.UUID, user_id: uuid.UUID
) -> list[Project]:
    """All non-archived Projects in this Workspace visible to this user.

    Per plan §5 hybrid model:
    - `workspace`-visibility projects: visible if user is a Workspace member
    - `private` projects: visible only if user is a Project member
    """
    stmt = (
        select(Project)
        .where(
            Project.workspace_id == workspace_id,
            Project.archived_at.is_(None),
            or_(
                Project.visibility == ProjectVisibility.workspace,
                Project.id.in_(
                    select(ProjectMember.project_id).where(ProjectMember.user_id == user_id)
                ),
            ),
        )
        .order_by(Project.created_at.desc())
    )

    # Also require user to be a Workspace member at all (else no access regardless)
    workspace_membership = await session.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    if workspace_membership.scalar_one_or_none() is None:
        return []

    result = await session.execute(stmt)
    return list(result.scalars())


async def archive_project(session: AsyncSession, project_id: uuid.UUID) -> Project:
    proj = await get_project(session, project_id)
    proj.archived_at = datetime.now(UTC)
    await session.flush()
    return proj
