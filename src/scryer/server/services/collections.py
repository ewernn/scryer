"""Collection service: bundle pointers to scryer resources."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.models.collab import Collection, CollectionMember
from scryer.server.models.enums import CollectionPurpose
from scryer.server.services.errors import ConflictError, NotFoundError
from scryer.server.services.workspaces import validate_slug


async def create_collection(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    slug: str,
    name: str,
    description: str | None = None,
    purpose: CollectionPurpose = CollectionPurpose.investigation,
) -> Collection:
    validate_slug(slug)
    if (
        await session.execute(
            select(Collection).where(Collection.project_id == project_id, Collection.slug == slug)
        )
    ).scalar_one_or_none():
        raise ConflictError(f"Collection slug {slug!r} already exists in project")
    c = Collection(
        project_id=project_id,
        slug=slug,
        name=name,
        description=description,
        purpose=purpose,
    )
    session.add(c)
    await session.flush()
    return c


async def add_member(
    session: AsyncSession,
    *,
    collection_id: uuid.UUID,
    member_type: str,
    member_id: uuid.UUID,
    position: int = 0,
    group: str | None = None,
    note: str | None = None,
) -> CollectionMember:
    if not await session.get(Collection, collection_id):
        raise NotFoundError("collection", str(collection_id))
    m = CollectionMember(
        collection_id=collection_id,
        member_type=member_type,
        member_id=member_id,
        position=position,
        group=group,
        note=note,
    )
    session.add(m)
    await session.flush()
    return m


async def list_collections(session: AsyncSession, project_id: uuid.UUID) -> list[Collection]:
    stmt = (
        select(Collection)
        .where(Collection.project_id == project_id)
        .order_by(Collection.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars())


async def list_members(session: AsyncSession, collection_id: uuid.UUID) -> list[CollectionMember]:
    stmt = (
        select(CollectionMember)
        .where(CollectionMember.collection_id == collection_id)
        .order_by(CollectionMember.position)
    )
    return list((await session.execute(stmt)).scalars())
