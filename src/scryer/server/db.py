"""SQLAlchemy 2.x async engine + session factory.

Engine is built once per app via the lifespan handler and attached to
`app.state.engine` / `app.state.session_factory`. Routes get a session via
`Depends(get_session)`. No module-global cache — tests can swap DATABASE_URL
by building a fresh engine per fixture.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

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


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        yield session
