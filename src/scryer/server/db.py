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


async def apply_workspace_context(
    session: AsyncSession,
    workspace_id: str | object,
    *,
    user_id: str | object | None = None,
) -> None:
    """Apply RLS GUCs to the session's CURRENT transaction immediately AND
    stash in session.info for any future transactions.

    The `after_begin` listener handles the case where session.info is already
    set when the transaction opens. This helper is the late-binding companion:
    when workspace_id only becomes known mid-flow (HTTP routes resolve the
    workspace via `{workspace_slug}` param after the dep chain has already
    opened a session; signup creates a personal workspace partway through
    the request), call this instead of just mutating session.info — that
    mutation alone won't reach the open transaction's SET LOCAL.

    Idempotent: safe to call repeatedly, including after the listener has
    already fired with the same value."""
    session.info["workspace_id"] = workspace_id
    await session.execute(
        text("SELECT set_config('app.current_workspace_id', :wid, true)"),
        {"wid": str(workspace_id)},
    )
    if user_id is not None:
        session.info["current_user_id"] = user_id
        await session.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": str(user_id)},
        )


@asynccontextmanager
async def with_workspace_context(
    session: AsyncSession,
    workspace_id: str | object,
    *,
    user_id: str | object | None = None,
) -> AsyncIterator[None]:
    """Async ctx-mgr wrapper around `apply_workspace_context` for non-HTTP
    callers (cron workers, tests, internal scripts). Sets the GUC for the
    enclosed block; subsequent transactions in the same session inherit
    via session.info."""
    await apply_workspace_context(session, workspace_id, user_id=user_id)
    try:
        yield
    finally:
        # Don't pop — subsequent transactions in the same session may want
        # the same context. Caller can override or clear explicitly.
        pass
