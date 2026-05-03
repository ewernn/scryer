"""Dataset service: push (create new version), get latest/specific, list,
add records, archive."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.models.eval import Dataset, DatasetRecord
from scryer.server.services._versioned import (
    content_hash,
    get_latest,
    get_version,
    list_latest_per_slug,
    next_version,
)
from scryer.server.services.errors import ConflictError, NotFoundError


async def push_dataset(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    slug: str,
    name: str,
    records: list[dict[str, Any]],
    description: str | None = None,
    schema_json: dict[str, Any] | None = None,
) -> Dataset:
    """Create a new Dataset version. records: list of {inputs, expected?, metadata?}.

    Each record gets an auto-incrementing record_id. Two pushes with identical
    contents produce identical content_hash but different versions (immutable).
    """
    if not records:
        raise ConflictError("Dataset must contain at least one record")

    version, parent_id = await next_version(session, Dataset, project_id=project_id, slug=slug)
    # FROZEN CONTRACT (Dataset.content_hash): schema_json + records.
    # name/description/record_count are metadata (excluded). Lock-in test:
    # tests/test_content_hash_stability.py.
    h = content_hash(
        {
            "schema_json": schema_json,
            "records": records,
        }
    )
    ds = Dataset(
        project_id=project_id,
        slug=slug,
        version=version,
        content_hash=h,
        parent_id=parent_id,
        name=name,
        description=description,
        schema_json=schema_json,
        record_count=len(records),
    )
    session.add(ds)
    await session.flush()

    for i, rec in enumerate(records, start=1):
        session.add(
            DatasetRecord(
                dataset_id=ds.id,
                record_id=i,
                inputs=rec["inputs"],
                expected=rec.get("expected"),
                metadata_json=rec.get("metadata"),
            )
        )
    await session.flush()
    return ds


async def get_dataset(session: AsyncSession, dataset_id: uuid.UUID) -> Dataset:
    ds = await session.get(Dataset, dataset_id)
    if ds is None or ds.archived_at is not None:
        raise NotFoundError("dataset", str(dataset_id))
    return ds


async def get_dataset_latest(session: AsyncSession, *, project_id: uuid.UUID, slug: str) -> Dataset:
    return await get_latest(session, Dataset, project_id=project_id, slug=slug)


async def get_dataset_version(
    session: AsyncSession, *, project_id: uuid.UUID, slug: str, version: int
) -> Dataset:
    return await get_version(session, Dataset, project_id=project_id, slug=slug, version=version)


async def list_datasets(session: AsyncSession, *, project_id: uuid.UUID) -> list[Dataset]:
    return await list_latest_per_slug(session, Dataset, project_id=project_id)


async def list_records(
    session: AsyncSession, dataset_id: uuid.UUID, *, limit: int = 1000, offset: int = 0
) -> list[DatasetRecord]:
    stmt = (
        select(DatasetRecord)
        .where(DatasetRecord.dataset_id == dataset_id)
        .order_by(DatasetRecord.record_id)
        .limit(limit)
        .offset(offset)
    )
    return list((await session.execute(stmt)).scalars())


async def count_records(session: AsyncSession, dataset_id: uuid.UUID) -> int:
    stmt = (
        select(func.count())
        .select_from(DatasetRecord)
        .where(DatasetRecord.dataset_id == dataset_id)
    )
    return int((await session.execute(stmt)).scalar_one())


async def archive_dataset(session: AsyncSession, dataset_id: uuid.UUID) -> Dataset:
    ds = await get_dataset(session, dataset_id)
    ds.archived_at = datetime.now(UTC)
    await session.flush()
    return ds
