"""GET /api/v1/me — agent first-call discovery endpoint per plan §12."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.auth import Principal, get_principal
from scryer.server.db import apply_workspace_context, get_session
from scryer.server.models.eval import Dataset, Scorer
from scryer.server.services.access import set_user_context_dep
from scryer.server.services.projects import list_projects_for_user_in_workspace
from scryer.server.services.workspaces import list_workspaces_for_user

router = APIRouter(tags=["meta"], dependencies=[Depends(set_user_context_dep)])


class IdentityOut(BaseModel):
    id: str
    kind: str
    name: str | None
    api_key_id: str | None


class TopResourcesOut(BaseModel):
    workspaces: list[dict[str, Any]]
    projects: list[dict[str, Any]]
    datasets: list[dict[str, Any]]
    scorers: list[dict[str, Any]]


class MeResponse(BaseModel):
    identity: IdentityOut
    permissions: list[str]
    top_resources: TopResourcesOut
    api_version: str
    docs: str
    server_time: datetime


_API_VERSION = "2026-05-02"
_DOCS_URL = "https://scryer.io/docs/agent-guide"


@router.get(
    "/me",
    response_model=MeResponse,
    operation_id="me",
    summary="Agent first-call: identity + permissions + top resources",
    description=(
        "Eliminates bootstrap roundtrips for agents. Returns the calling "
        "principal, scope grants, and the 5 most-recent workspaces / "
        "projects / datasets / scorers visible to the principal."
    ),
)
async def me(
    principal: Annotated[Principal, Depends(get_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MeResponse:
    workspaces = await list_workspaces_for_user(session, principal.id)
    ws_summaries = [{"id": str(w.id), "slug": w.slug, "name": w.name} for w in workspaces[:5]]
    projects: list[dict[str, Any]] = []
    datasets: list[dict[str, Any]] = []
    scorers: list[dict[str, Any]] = []

    # RLS: each workspace scopes its own projects/datasets/scorers via the
    # current_workspace_id GUC. Switch context per workspace before the
    # per-workspace SELECTs (otherwise rows are silently filtered).
    for w in workspaces[:3]:  # cap at 3 workspaces × 5 projects = 15 lookups max
        await apply_workspace_context(session, w.id, user_id=principal.id)
        projs = await list_projects_for_user_in_workspace(
            session, workspace_id=w.id, user_id=principal.id
        )
        ws_project_ids = [str(p.id) for p in projs[:5]]
        for p in projs[:5]:
            projects.append(
                {
                    "id": str(p.id),
                    "slug": p.slug,
                    "workspace_slug": w.slug,
                    "name": p.name,
                }
            )
        if ws_project_ids and len(datasets) < 5:
            import uuid as _u

            ds_rows = list(
                (
                    await session.execute(
                        select(Dataset)
                        .where(Dataset.project_id.in_([_u.UUID(p) for p in ws_project_ids]))
                        .where(Dataset.archived_at.is_(None))
                        .order_by(Dataset.created_at.desc())
                        .limit(5 - len(datasets))
                    )
                ).scalars()
            )
            datasets.extend(
                {"id": str(d.id), "slug": d.slug, "version": d.version, "name": d.name}
                for d in ds_rows
            )
        if ws_project_ids and len(scorers) < 5:
            import uuid as _u

            sc_rows = list(
                (
                    await session.execute(
                        select(Scorer)
                        .where(Scorer.project_id.in_([_u.UUID(p) for p in ws_project_ids]))
                        .where(Scorer.archived_at.is_(None))
                        .order_by(Scorer.created_at.desc())
                        .limit(5 - len(scorers))
                    )
                ).scalars()
            )
            scorers.extend(
                {"id": str(s.id), "slug": s.slug, "version": s.version, "name": s.name}
                for s in sc_rows
            )

    return MeResponse(
        identity=IdentityOut(
            id=str(principal.id),
            kind=principal.kind.value,
            name=None,
            api_key_id=str(principal.api_key_id) if principal.api_key_id else None,
        ),
        permissions=sorted(s.value for s in principal.scopes),
        top_resources=TopResourcesOut(
            workspaces=ws_summaries,
            projects=projects[:5],
            datasets=datasets,
            scorers=scorers,
        ),
        api_version=_API_VERSION,
        docs=_DOCS_URL,
        server_time=datetime.now(__import__("datetime").UTC),
    )
