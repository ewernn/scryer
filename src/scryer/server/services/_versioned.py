"""Shared helpers for VersionedMixin-using services (Dataset, Scorer, Agent,
Tool, Prompt, Task). Avoids duplicating bump/get/list per noun."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.models.base import Base
from scryer.server.services.errors import NotFoundError

T = TypeVar("T", bound=Base)


def content_hash(payload: dict[str, Any]) -> str:
    """sha256 hex of canonical-JSON-encoded payload. Sorted keys → deterministic."""
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


async def next_version(
    session: AsyncSession,
    model: type[T],
    *,
    project_id: uuid.UUID,
    slug: str,
) -> tuple[int, uuid.UUID | None]:
    """Returns (next_version, parent_id) for a (project, slug) pair.

    parent_id is the latest existing row's id, or None if this is v1.
    """
    stmt = (
        select(model)
        .where(model.project_id == project_id, model.slug == slug)  # type: ignore[attr-defined]
        .order_by(model.version.desc())  # type: ignore[attr-defined]
        .limit(1)
    )
    latest = (await session.execute(stmt)).scalar_one_or_none()
    if latest is None:
        return 1, None
    return latest.version + 1, latest.id  # type: ignore[attr-defined]


async def get_latest(
    session: AsyncSession,
    model: type[T],
    *,
    project_id: uuid.UUID,
    slug: str,
) -> T:
    stmt = (
        select(model)
        .where(model.project_id == project_id, model.slug == slug)  # type: ignore[attr-defined]
        .where(model.archived_at.is_(None))  # type: ignore[attr-defined]
        .order_by(model.version.desc())  # type: ignore[attr-defined]
        .limit(1)
    )
    latest = (await session.execute(stmt)).scalar_one_or_none()
    if latest is None:
        raise NotFoundError(model.__tablename__, f"{project_id}/{slug}")
    return latest


async def get_version(
    session: AsyncSession,
    model: type[T],
    *,
    project_id: uuid.UUID,
    slug: str,
    version: int,
) -> T:
    stmt = select(model).where(
        model.project_id == project_id,  # type: ignore[attr-defined]
        model.slug == slug,  # type: ignore[attr-defined]
        model.version == version,  # type: ignore[attr-defined]
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise NotFoundError(model.__tablename__, f"{project_id}/{slug}@v{version}")
    return row


async def list_latest_per_slug(
    session: AsyncSession,
    model: type[T],
    *,
    project_id: uuid.UUID,
) -> list[T]:
    """Return only the latest version of each slug in the project."""
    from sqlalchemy import func

    subq = (
        select(model.slug, func.max(model.version).label("v"))  # type: ignore[attr-defined]
        .where(model.project_id == project_id, model.archived_at.is_(None))  # type: ignore[attr-defined]
        .group_by(model.slug)  # type: ignore[attr-defined]
        .subquery()
    )
    stmt = select(model).join(
        subq,
        (model.slug == subq.c.slug) & (model.version == subq.c.v),  # type: ignore[attr-defined]
    )
    return list((await session.execute(stmt)).scalars())
