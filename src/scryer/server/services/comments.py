"""Comment service: write, list, edit (with version history)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.models.collab import Comment, CommentVersion
from scryer.server.models.enums import ActorKind, CommentKind, PrincipalKind
from scryer.server.services.errors import NotFoundError, PermissionError


async def write_comment(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    body: str,
    author_user_id: uuid.UUID | None = None,
    author_service_account_id: uuid.UUID | None = None,
    kind: CommentKind = CommentKind.user,
    resource_type: str | None = None,
    resource_id: uuid.UUID | None = None,
    structured: dict[str, Any] | None = None,
    references: list[dict[str, Any]] | None = None,
    mentions: list[uuid.UUID] | None = None,
    reply_to_comment_id: uuid.UUID | None = None,
) -> Comment:
    if author_user_id is not None:
        author_kind = ActorKind.user
    elif author_service_account_id is not None:
        author_kind = ActorKind.service_account
    elif kind == CommentKind.system:
        author_kind = ActorKind.system
    else:
        from scryer.server.services.errors import ValidationError

        raise ValidationError("Comment requires either author_user_id or author_service_account_id")

    c = Comment(
        project_id=project_id,
        resource_type=resource_type,
        resource_id=resource_id,
        author_kind=author_kind,
        author_user_id=author_user_id,
        author_service_account_id=author_service_account_id,
        kind=kind,
        body=body,
        structured=structured,
        references=references,
        mentions=mentions,
        reply_to_comment_id=reply_to_comment_id,
    )
    session.add(c)
    await session.flush()
    return c


async def write_system_comment(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    body: str,
    resource_type: str | None = None,
    resource_id: uuid.UUID | None = None,
    structured: dict[str, Any] | None = None,
) -> Comment:
    """Convenience for kind='system' auto-Comments per plan §8 layer 3."""
    return await write_comment(
        session,
        project_id=project_id,
        body=body,
        kind=CommentKind.system,
        resource_type=resource_type,
        resource_id=resource_id,
        structured=structured,
    )


async def edit_comment(
    session: AsyncSession,
    *,
    comment_id: uuid.UUID,
    body: str,
    structured: dict[str, Any] | None = None,
    editor_user_id: uuid.UUID | None = None,
    editor_principal_kind: PrincipalKind | None = None,
) -> Comment:
    """Edit a Comment; previous body archived in CommentVersion."""
    c = await session.get(Comment, comment_id)
    if c is None:
        raise NotFoundError("comment", str(comment_id))
    # System comments can't be edited
    if c.kind == CommentKind.system:
        raise PermissionError("System comments cannot be edited")
    # Authorship check: only the original author can edit (v0)
    if editor_principal_kind == PrincipalKind.user and c.author_user_id != editor_user_id:
        raise PermissionError("Only the comment author can edit")

    next_v = (
        await session.execute(
            select(CommentVersion)
            .where(CommentVersion.comment_id == comment_id)
            .order_by(CommentVersion.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    next_version = (next_v.version + 1) if next_v else 1
    session.add(
        CommentVersion(
            comment_id=comment_id,
            version=next_version,
            body=c.body,
            structured=c.structured,
            edited_at=datetime.now(UTC),
        )
    )
    c.body = body
    if structured is not None:
        c.structured = structured
    await session.flush()
    return c


async def list_comments_for_resource(
    session: AsyncSession, *, resource_type: str, resource_id: uuid.UUID
) -> list[Comment]:
    stmt = (
        select(Comment)
        .where(
            Comment.resource_type == resource_type,
            Comment.resource_id == resource_id,
        )
        .order_by(Comment.created_at)
    )
    return list((await session.execute(stmt)).scalars())


async def list_comments_in_project(
    session: AsyncSession, project_id: uuid.UUID, *, limit: int = 100
) -> list[Comment]:
    stmt = (
        select(Comment)
        .where(Comment.project_id == project_id)
        .order_by(Comment.created_at.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars())
