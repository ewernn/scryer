"""Server-rendered web UI: Jinja + htmx + Tailwind (CDN, v0).

Per plan §4: API-first separation. Web routes call service functions
DIRECTLY (no internal HTTP). Same JSON API serves CLI/SDK/dashboard.

Auth via httponly cookie set by /web/login → POST. JWT in cookie.
Cookie `secure` flag is env-driven so local dev over plain HTTP works.
"""

from __future__ import annotations

import math
import os
import uuid
from pathlib import Path
from typing import Annotated

import jwt
from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.db import get_session
from scryer.server.models.enums import ApiScope, PrincipalKind
from scryer.server.services.errors import AuthError
from scryer.server.services.rate_limit import check_login_rate
from scryer.server.services.runs import get_run, list_results
from scryer.server.services.security import (
    generate_csrf,
    issue_access_jwt,
    verify_access_jwt,
    verify_csrf,
)
from scryer.server.services.users import authenticate, get_user
from scryer.server.services.workspaces import list_workspaces_for_user

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
COOKIE_NAME = "scryer_session"
_COOKIE_SECURE = bool(os.environ.get("RAILWAY_ENVIRONMENT")) or os.environ.get("ENV") == "prod"

router = APIRouter(tags=["web"], include_in_schema=False)


async def _require_user(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    scryer_session: Annotated[str | None, Cookie()] = None,
) -> uuid.UUID:
    if not scryer_session:
        raise HTTPException(status.HTTP_303_SEE_OTHER, headers={"location": "/web/login"})
    try:
        claims = verify_access_jwt(scryer_session)
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_303_SEE_OTHER, headers={"location": "/web/login"}) from exc
    user_id = uuid.UUID(claims["sub"])
    await get_user(session, user_id)  # validate still active
    request.state.csrf_token = generate_csrf(scryer_session)
    return user_id


@router.get("/web", response_model=None)
async def root() -> RedirectResponse:
    return RedirectResponse("/web/workspaces", status_code=303)


@router.get("/web/login", response_class=HTMLResponse)
async def login_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/web/login", response_model=None)
async def login_submit(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
) -> HTMLResponse | RedirectResponse:
    from scryer.server.services.errors import PermissionError as ScryerPermErr

    client_host = request.client.host if request.client else "unknown"
    try:
        check_login_rate(email=email, ip=client_host)
        user = await authenticate(session, email=email, password=password)
    except (AuthError, ScryerPermErr) as exc:
        return templates.TemplateResponse(
            request, "login.html", {"error": str(exc)}, status_code=401
        )
    # Other exceptions propagate → central handler returns 500
    await session.commit()
    token = issue_access_jwt(str(user.id))
    resp = RedirectResponse("/web/workspaces", status_code=303)
    resp.set_cookie(COOKIE_NAME, token, httponly=True, secure=_COOKIE_SECURE, samesite="lax")
    return resp


@router.post("/web/logout")
async def logout(
    csrf: Annotated[str, Form()],
    scryer_session: Annotated[str | None, Cookie()] = None,
) -> RedirectResponse:
    if not scryer_session or not verify_csrf(scryer_session, csrf):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")
    resp = RedirectResponse("/web/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp


@router.get("/web/workspaces", response_class=HTMLResponse)
async def workspaces_page(
    request: Request,
    user_id: Annotated[uuid.UUID, Depends(_require_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HTMLResponse:
    workspaces = await list_workspaces_for_user(session, user_id)
    return templates.TemplateResponse(
        request,
        "workspaces.html",
        {
            "workspaces": workspaces,
            "user_id": str(user_id),
            "csrf_token": request.state.csrf_token,
        },
    )


@router.get("/web/workspaces/{ws_slug}/projects/{proj_slug}", response_class=HTMLResponse)
async def project_page(
    request: Request,
    ws_slug: str,
    proj_slug: str,
    user_id: Annotated[uuid.UUID, Depends(_require_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HTMLResponse:
    from scryer.server.auth import Principal
    from scryer.server.services.access import get_project_by_slug_path
    from scryer.server.services.datasets import list_datasets
    from scryer.server.services.scorers import list_scorers
    from scryer.server.services.tasks import list_tasks

    principal = Principal(
        id=user_id, kind=PrincipalKind.user, scopes=frozenset({ApiScope.read, ApiScope.write})
    )
    proj = await get_project_by_slug_path(
        session, principal, workspace_slug=ws_slug, project_slug=proj_slug
    )
    datasets = await list_datasets(session, project_id=proj.id)
    scorers = await list_scorers(session, project_id=proj.id)
    tasks = await list_tasks(session, project_id=proj.id)
    return templates.TemplateResponse(
        request,
        "project.html",
        {
            "ws_slug": ws_slug,
            "proj_slug": proj_slug,
            "project": proj,
            "datasets": datasets,
            "scorers": scorers,
            "tasks": tasks,
            "csrf_token": request.state.csrf_token,
        },
    )


@router.get("/web/workspaces/{ws_slug}/runs/{run_id}", response_class=HTMLResponse)
async def run_page(
    request: Request,
    ws_slug: str,
    run_id: str,
    user_id: Annotated[uuid.UUID, Depends(_require_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=500)] = 50,
) -> HTMLResponse:
    """Workspace-scoped run page. The workspace_slug in the URL lets us
    set the RLS GUC via apply_workspace_context BEFORE querying the
    RLS-policied runs / results / comments tables (without context FORCE
    RLS would silently 404 every page)."""
    from scryer.server.auth import Principal
    from scryer.server.db import apply_workspace_context
    from scryer.server.services.access import assert_project_access, assert_workspace_member
    from scryer.server.services.comments import list_comments_for_resource
    from scryer.server.services.workspaces import get_workspace_by_slug

    principal = Principal(id=user_id, kind=PrincipalKind.user, scopes=frozenset({ApiScope.read}))
    ws = await get_workspace_by_slug(session, ws_slug)
    await apply_workspace_context(session, ws.id, user_id=user_id)
    await assert_workspace_member(session, principal, ws.id)

    rid = uuid.UUID(run_id)
    run = await get_run(session, rid)
    # IDOR fix: principal must have access to the project this Run belongs to
    await assert_project_access(session, principal, run.project_id)
    offset = (page - 1) * per_page
    results = await list_results(session, rid, limit=per_page, offset=offset)
    total_records = run.n_records or 0
    total_pages = max(1, math.ceil(total_records / per_page)) if total_records else 1
    comments = await list_comments_for_resource(session, resource_type="run", resource_id=rid)
    return templates.TemplateResponse(
        request,
        "run.html",
        {
            "run": run,
            "results": results,
            "comments": comments,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "total_records": total_records,
            "csrf_token": request.state.csrf_token,
        },
    )
