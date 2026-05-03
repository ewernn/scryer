"""Dataset endpoints: push, get, list, list records."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.auth import Principal, get_principal
from scryer.server.db import get_session
from scryer.server.services.access import get_project_by_slug_path
from scryer.server.services.datasets import (
    get_dataset,
    get_dataset_latest,
    list_datasets,
    list_records,
    push_dataset,
)
from scryer.server.services.idempotency import (
    capture_idempotency_response,
    check_idempotency,
)

router = APIRouter(tags=["datasets"])


class RecordIn(BaseModel):
    inputs: dict[str, Any]
    expected: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class DatasetPushRequest(BaseModel):
    slug: str
    name: str
    records: list[RecordIn]
    description: str | None = None
    # Renamed to avoid clash with BaseModel.schema_json method
    record_schema: dict[str, Any] | None = None


class DatasetOut(BaseModel):
    id: str
    slug: str
    version: int
    content_hash: str
    name: str
    description: str | None
    record_count: int
    parent_id: str | None
    created_at: datetime


class RecordOut(BaseModel):
    record_id: int
    inputs: dict[str, Any]
    expected: dict[str, Any] | None
    metadata: dict[str, Any] | None


def _to_out(ds: Any) -> DatasetOut:
    return DatasetOut(
        id=str(ds.id),
        slug=ds.slug,
        version=ds.version,
        content_hash=ds.content_hash,
        name=ds.name,
        description=ds.description,
        record_count=ds.record_count,
        parent_id=str(ds.parent_id) if ds.parent_id else None,
        created_at=ds.created_at,
    )


@router.post(
    "/workspaces/{workspace_slug}/projects/{project_slug}/datasets",
    response_model=DatasetOut,
    operation_id="datasets.push",
    summary="Push a new Dataset version",
    dependencies=[Depends(check_idempotency)],
)
async def push(
    request: Request,
    workspace_slug: str,
    project_slug: str,
    body: DatasetPushRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
    background_tasks: BackgroundTasks,
) -> DatasetOut:
    proj = await get_project_by_slug_path(
        session, principal, workspace_slug=workspace_slug, project_slug=project_slug
    )
    ds = await push_dataset(
        session,
        project_id=proj.id,
        slug=body.slug,
        name=body.name,
        records=[r.model_dump() for r in body.records],
        description=body.description,
        schema_json=body.record_schema,
    )
    await session.commit()
    out = _to_out(ds)
    capture_idempotency_response(
        background_tasks,
        request,
        getattr(request.app.state, "session_factory", None),
        status_code=200,
        body=out.model_dump(mode="json"),
    )
    return out


@router.get(
    "/workspaces/{workspace_slug}/projects/{project_slug}/datasets",
    response_model=list[DatasetOut],
    operation_id="datasets.list",
)
async def list_(
    workspace_slug: str,
    project_slug: str,
    principal: Annotated[Principal, Depends(get_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[DatasetOut]:
    proj = await get_project_by_slug_path(
        session, principal, workspace_slug=workspace_slug, project_slug=project_slug
    )
    rows = await list_datasets(session, project_id=proj.id)
    return [_to_out(r) for r in rows]


@router.get(
    "/workspaces/{workspace_slug}/projects/{project_slug}/datasets/{slug}",
    response_model=DatasetOut,
    operation_id="datasets.get_latest",
)
async def get_latest(
    workspace_slug: str,
    project_slug: str,
    slug: str,
    principal: Annotated[Principal, Depends(get_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DatasetOut:
    proj = await get_project_by_slug_path(
        session, principal, workspace_slug=workspace_slug, project_slug=project_slug
    )
    return _to_out(await get_dataset_latest(session, project_id=proj.id, slug=slug))


@router.get(
    "/datasets/{dataset_id}/records",
    response_model=list[RecordOut],
    operation_id="datasets.list_records",
)
async def list_records_endpoint(
    dataset_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(1000, ge=1, le=10000),
    offset: int = Query(0, ge=0),
) -> list[RecordOut]:
    import uuid as _u

    ds = await get_dataset(session, _u.UUID(dataset_id))
    # authz: project access
    from scryer.server.services.access import assert_project_access

    await assert_project_access(session, principal, ds.project_id)
    rows = await list_records(session, ds.id, limit=limit, offset=offset)
    return [
        RecordOut(
            record_id=r.record_id,
            inputs=r.inputs,
            expected=r.expected,
            metadata=r.metadata_json,
        )
        for r in rows
    ]
