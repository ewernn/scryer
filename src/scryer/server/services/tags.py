"""Tag + ResourceTag service: workspace-scoped tags applied to any resource."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.models.audit import ResourceTag, Tag
from scryer.server.services.errors import ConflictError, NotFoundError


async def create_tag(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    name: str,
    color: str | None = None,
) -> Tag:
    existing = await session.execute(
        select(Tag).where(Tag.workspace_id == workspace_id, Tag.name == name)
    )
    if existing.scalar_one_or_none():
        raise ConflictError(f"Tag {name!r} already exists in workspace")
    tag = Tag(workspace_id=workspace_id, name=name, color=color)
    session.add(tag)
    await session.flush()
    return tag


async def list_tags(session: AsyncSession, workspace_id: uuid.UUID) -> list[Tag]:
    stmt = select(Tag).where(Tag.workspace_id == workspace_id).order_by(Tag.name)
    return list((await session.execute(stmt)).scalars())


async def apply_tag(
    session: AsyncSession,
    *,
    tag_id: uuid.UUID,
    resource_type: str,
    resource_id: uuid.UUID,
) -> ResourceTag:
    if not await session.get(Tag, tag_id):
        raise NotFoundError("tag", str(tag_id))
    existing = await session.execute(
        select(ResourceTag).where(
            ResourceTag.tag_id == tag_id,
            ResourceTag.resource_type == resource_type,
            ResourceTag.resource_id == resource_id,
        )
    )
    if (rt := existing.scalar_one_or_none()) is not None:
        return rt
    rt = ResourceTag(tag_id=tag_id, resource_type=resource_type, resource_id=resource_id)
    session.add(rt)
    await session.flush()
    return rt


async def remove_tag(
    session: AsyncSession,
    *,
    tag_id: uuid.UUID,
    resource_type: str,
    resource_id: uuid.UUID,
) -> None:
    rt = (
        await session.execute(
            select(ResourceTag).where(
                ResourceTag.tag_id == tag_id,
                ResourceTag.resource_type == resource_type,
                ResourceTag.resource_id == resource_id,
            )
        )
    ).scalar_one_or_none()
    if rt is not None:
        await session.delete(rt)
        await session.flush()


async def list_tags_for_resource(
    session: AsyncSession, *, resource_type: str, resource_id: uuid.UUID
) -> list[Tag]:
    stmt = (
        select(Tag)
        .join(ResourceTag, ResourceTag.tag_id == Tag.id)
        .where(
            ResourceTag.resource_type == resource_type,
            ResourceTag.resource_id == resource_id,
        )
        .order_by(Tag.name)
    )
    return list((await session.execute(stmt)).scalars())
