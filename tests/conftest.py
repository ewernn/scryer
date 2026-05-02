"""Pytest async fixtures for scryer.

**Schema isolation strategy.** We run against the same Neon DB as dev. To avoid
tests dropping/recreating real tables, we:
1. Generate a unique schema name per test session: `scryer_test_<random>`.
2. Set `search_path` on the connection so all `CREATE TABLE`/`SELECT`/etc.
   target that schema.
3. `Base.metadata.create_all()` creates all 14 cluster-1 tables in the test
   schema; `drop_all()` + `DROP SCHEMA CASCADE` cleans up after.
4. Per-test rollback fixture wraps each test in a savepoint inside the
   session's outer transaction (no commits leak).

For CI: a Neon branch per run is cleaner — set SCRYER_TEST_DATABASE_URL to the
branch URL (which has its own `public` schema). Falls back to schema isolation
if not set.

References:
- pytest-asyncio ≥0.23: avoid custom event_loop fixture; use loop_scope=session.
- SQLAlchemy AsyncSession: join_transaction_mode="create_savepoint" + event
  listener to re-open savepoint after commits inside test code.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from scryer.server.app import create_app
from scryer.server.db import get_session
from scryer.server.models import Base


def _test_database_url() -> str:
    url = os.environ.get("SCRYER_TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("SCRYER_TEST_DATABASE_URL (or DATABASE_URL) must be set for tests.")
    return url


@pytest_asyncio.fixture(scope="session")
async def test_schema() -> str:
    return f"scryer_test_{secrets.token_hex(6)}"


@pytest_asyncio.fixture(scope="session")
async def engine(test_schema: str) -> AsyncIterator[AsyncEngine]:
    """One engine per test session, bound to a unique schema for isolation.

    All metadata operations (`create_all`, queries) target the test schema via
    `search_path`. Production tables in `public` are not touched.
    """
    eng = create_async_engine(
        _test_database_url(),
        echo=False,
        connect_args={"server_settings": {"search_path": test_schema}},
    )

    # Ensure schema exists; create tables in it
    async with eng.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{test_schema}"'))
        await conn.run_sync(Base.metadata.create_all)

    yield eng

    # Tear down
    async with eng.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA "{test_schema}" CASCADE'))
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Per-test transactional session. All writes are rolled back on teardown.

    `join_transaction_mode="create_savepoint"` makes `session.commit()` calls
    inside business logic emit RELEASE SAVEPOINT instead of COMMIT, so the
    outer transaction stays open for rollback.
    """
    async with engine.connect() as conn:
        await conn.begin()
        await conn.begin_nested()

        async_session = AsyncSession(
            bind=conn,
            join_transaction_mode="create_savepoint",
        )

        @event.listens_for(async_session.sync_session, "after_transaction_end")
        def _reopen_savepoint(_sess, _transaction):  # type: ignore[no-untyped-def]
            # After business logic commits its savepoint, immediately reopen one
            # so subsequent writes have somewhere to go.
            if conn.sync_connection is None:
                return
            if not conn.sync_connection.in_nested_transaction():
                conn.sync_connection.begin_nested()

        try:
            yield async_session
        finally:
            event.remove(async_session.sync_session, "after_transaction_end", _reopen_savepoint)
            await async_session.close()
            await conn.rollback()


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """ASGI test client whose `Depends(get_session)` is overridden to share the
    test's transactional session — endpoint writes are visible in test
    assertions and roll back on teardown.
    """
    app = create_app()

    async def _override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_session, None)
