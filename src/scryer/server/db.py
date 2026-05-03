"""SQLAlchemy 2.x async engine + session factory + RLS context.

Engine is built once per app via the lifespan handler and attached to
`app.state.engine` / `app.state.session_factory`. Routes get a session via
`Depends(get_session)`. No module-global cache — tests can swap DATABASE_URL
by building a fresh engine per fixture.

RLS context: every transaction injects `SET LOCAL app.current_workspace_id`
from `session.info["workspace_id"]` via an `after_begin` SQLAlchemy event
listener. The dependency `get_session` populates that key from
`request.state.workspace_id` if set (by `require_workspace_context` Depends
or middleware). Without a workspace_id, RLS-policied tables return zero
rows — fail-closed.
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
def _set_rls_workspace_context(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
    """SET LOCAL app.current_workspace_id at every transaction start.

    Module-level listener on Session — fires for every session in the process.
    The body is conditional on `session.info["workspace_id"]` so sessions
    that don't need RLS context (auth, healthz) are unaffected.

    Without a workspace_id, RLS-policied tables return zero rows — fail-closed."""
    ws_id = session.info.get("workspace_id")
    if ws_id is not None:
        # Use set_config() function form — `SET LOCAL var = value` is a server
        # command and doesn't accept parameter placeholders. set_config(name,
        # value, is_local=true) is the parameter-friendly equivalent.
        connection.execute(
            text("SELECT set_config('app.current_workspace_id', :wid, true)"),
            {"wid": str(ws_id)},
        )


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        ws_id = getattr(request.state, "workspace_id", None)
        if ws_id is not None:
            session.info["workspace_id"] = ws_id
        yield session


@asynccontextmanager
async def with_workspace_context(
    session: AsyncSession, workspace_id: str | object
) -> AsyncIterator[None]:
    """Set RLS workspace context for callers that don't go through HTTP
    (cron workers, tests, internal scripts). The SET LOCAL applies for the
    transaction the next statement opens.

    Usage:
        async with with_workspace_context(session, ws.id):
            await session.execute(select(Run).where(...))
    """
    session.info["workspace_id"] = workspace_id
    try:
        yield
    finally:
        # Don't pop — subsequent transactions in the same session may want
        # the same context. Caller can override or clear explicitly.
        pass
