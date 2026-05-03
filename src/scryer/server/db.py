"""SQLAlchemy 2.x async engine + session factory + RLS context.

Engine is built once per app via the lifespan handler and attached to
`app.state.engine` / `app.state.session_factory`. Routes get a session via
`Depends(get_session)`. No module-global cache — tests can swap DATABASE_URL
by building a fresh engine per fixture.

RLS context: every transaction injects two GUCs via an `after_begin`
listener — `app.current_workspace_id` (from `session.info["workspace_id"]`)
and `app.current_user_id` (from `session.info["current_user_id"]`).

`require_workspace_from_path` (services/access.py) sets the workspace key
when a `{workspace_slug}` path resolves; `set_user_context_dep` (same
file) sets the user key on routes outside any workspace (auth, /me).

Without the relevant GUC, RLS-policied tables return zero rows. Fail-closed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Request
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session

from scryer.config import Settings, get_settings


def build_engine(database_url: str, settings: Settings | None = None) -> AsyncEngine:
    s = settings or get_settings()
    return create_async_engine(
        database_url,
        echo=False,
        pool_pre_ping=True,
        pool_size=s.db_pool_size,
        max_overflow=s.db_max_overflow,
        pool_timeout=s.db_pool_timeout,
        pool_recycle=s.db_pool_recycle,
    )


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)


@event.listens_for(Session, "after_begin")
def _set_rls_context(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
    """SET LOCAL app.current_{workspace,user}_id at every transaction start.

    Module-level listener on Session — fires for every session in the
    process. Each branch is conditional on the corresponding session.info
    key so sessions that don't need RLS context (the AuthN flow itself,
    healthz) are unaffected.

    Uses set_config() function form because `SET LOCAL var = value` is a
    server command that can't take parameter placeholders. set_config(name,
    value, is_local=true) is the parameter-friendly equivalent."""
    ws_id = session.info.get("workspace_id")
    if ws_id is not None:
        connection.execute(
            text("SELECT set_config('app.current_workspace_id', :wid, true)"),
            {"wid": str(ws_id)},
        )
    user_id = session.info.get("current_user_id")
    if user_id is not None:
        connection.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": str(user_id)},
        )


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        ws_id = getattr(request.state, "workspace_id", None)
        if ws_id is not None:
            session.info["workspace_id"] = ws_id
        user_id = getattr(request.state, "current_user_id", None)
        if user_id is not None:
            session.info["current_user_id"] = user_id
        yield session


@asynccontextmanager
async def with_workspace_context(
    session: AsyncSession,
    workspace_id: str | object,
    *,
    user_id: str | object | None = None,
) -> AsyncIterator[None]:
    """Set RLS context for callers that don't go through HTTP (cron workers,
    tests, internal scripts). Apply BEFORE the next statement opens a
    transaction; the listener consumes session.info on `after_begin`.

    Usage:
        async with with_workspace_context(session, ws.id):
            await session.execute(select(Run).where(...))

    Pass `user_id=` if your queries also need to satisfy auth-table RLS
    policies that gate on `app.current_user_id` (workspace_members,
    project_members)."""
    session.info["workspace_id"] = workspace_id
    if user_id is not None:
        session.info["current_user_id"] = user_id
    try:
        yield
    finally:
        # Don't pop — subsequent transactions in the same session may want
        # the same context. Caller can override or clear explicitly.
        pass
