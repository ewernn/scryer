"""Scorer service: push (new version), get, list, archive."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.models.eval import Scorer
from scryer.server.services._versioned import (
    content_hash,
    get_latest,
    get_version,
    list_latest_per_slug,
    next_version,
)
from scryer.server.services.errors import NotFoundError


async def push_scorer(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    slug: str,
    name: str,
    source_text: str,
    description: str | None = None,
    server_executable: bool = True,
) -> Scorer:
    version, parent_id = await next_version(session, Scorer, project_id=project_id, slug=slug)
    h = content_hash({"source_text": source_text, "server_executable": server_executable})
    s = Scorer(
        project_id=project_id,
        slug=slug,
        version=version,
        content_hash=h,
        parent_id=parent_id,
        name=name,
        description=description,
        source_text=source_text,
        server_executable=server_executable,
    )
    session.add(s)
    await session.flush()
    return s


async def get_scorer(session: AsyncSession, scorer_id: uuid.UUID) -> Scorer:
    s = await session.get(Scorer, scorer_id)
    if s is None or s.archived_at is not None:
        raise NotFoundError("scorer", str(scorer_id))
    return s


async def get_scorer_latest(session: AsyncSession, *, project_id: uuid.UUID, slug: str) -> Scorer:
    return await get_latest(session, Scorer, project_id=project_id, slug=slug)


async def get_scorer_version(
    session: AsyncSession, *, project_id: uuid.UUID, slug: str, version: int
) -> Scorer:
    return await get_version(session, Scorer, project_id=project_id, slug=slug, version=version)


async def list_scorers(session: AsyncSession, *, project_id: uuid.UUID) -> list[Scorer]:
    return await list_latest_per_slug(session, Scorer, project_id=project_id)


async def archive_scorer(session: AsyncSession, scorer_id: uuid.UUID) -> Scorer:
    s = await get_scorer(session, scorer_id)
    s.archived_at = datetime.now(UTC)
    await session.flush()
    return s
