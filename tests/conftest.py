"""Pytest async fixtures for scryer.

**Test DB strategy.** A docker postgres is started once per pytest session,
migrated via `alembic upgrade head` (so triggers, RLS policies, CHECK
constraints — everything that `Base.metadata.create_all` skips — are
present). Each test runs inside an outer transaction with an inner
SAVEPOINT; teardown rolls back. No state leaks between tests, no Neon
involvement.

For RLS: tests that need workspace context wrap their assertion block in
`async with workspace_context(session, ws_id):`. The `privileged_engine`
fixture connects with a BYPASSRLS role for cross-tenant setup data.

Override the docker port via `SCRYER_TEST_PG_PORT` env (default 54332,
distinct from the migration-test container's 54331 so both can run
concurrently).

References:
- pytest-asyncio ≥0.23: avoid custom event_loop fixture; use loop_scope=session.
- SQLAlchemy AsyncSession: join_transaction_mode="create_savepoint" + event
  listener to re-open savepoint after commits inside test code.
"""

from __future__ import annotations

import atexit
import os
import subprocess
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

# These imports happen AFTER the docker container starts (see _docker_pg_url
# bootstrap below) so SCRYER_TEST_DATABASE_URL is set before scryer.config
# is loaded.

_PG_PORT = int(os.environ.get("SCRYER_TEST_PG_PORT", "54332"))
_PG_IMAGE = os.environ.get("SCRYER_TEST_PG_IMAGE", "postgres:17-alpine")
# Fixed container name so a stale container from a crashed/killed pytest run
# is reliably caught by `docker rm -f` in _start_docker_pg.
_PG_CONTAINER = "scryer_pytest"


def _start_docker_pg() -> str:
    """Spin up postgres in docker, wait for ready, return the SQLAlchemy URL.
    Caller is responsible for cleanup via _stop_docker_pg(). Sets
    SCRYER_TEST_DATABASE_URL env var for downstream alembic env.py use."""
    subprocess.run(
        ["docker", "rm", "-f", _PG_CONTAINER],
        capture_output=True,
        check=False,
    )
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            _PG_CONTAINER,
            "-e",
            "POSTGRES_PASSWORD=test",
            "-e",
            "POSTGRES_DB=scryer_test",
            "-p",
            f"{_PG_PORT}:5432",
            _PG_IMAGE,
        ],
        check=True,
        capture_output=True,
    )
    # Wait for ready (max 30s).
    for _ in range(30):
        result = subprocess.run(
            ["docker", "exec", _PG_CONTAINER, "pg_isready", "-U", "postgres"],
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            break
        time.sleep(1)
    else:
        subprocess.run(["docker", "logs", _PG_CONTAINER], check=False)
        subprocess.run(["docker", "rm", "-f", _PG_CONTAINER], check=False)
        raise RuntimeError(f"docker postgres on :{_PG_PORT} never became ready")

    url = f"postgresql+asyncpg://postgres:test@localhost:{_PG_PORT}/scryer_test"
    os.environ["SCRYER_TEST_DATABASE_URL"] = url
    return url


def _stop_docker_pg() -> None:
    subprocess.run(["docker", "rm", "-f", _PG_CONTAINER], capture_output=True, check=False)


def _alembic_upgrade_head(url: str) -> None:
    """Run `alembic upgrade head` against `url`. Subprocess-isolated so the
    main process's SQLAlchemy state is unaffected."""
    env = os.environ.copy()
    env["SCRYER_TEST_DATABASE_URL"] = url
    subprocess.run(
        [os.path.expanduser("~/.local/bin/uv"), "run", "alembic", "upgrade", "head"],
        cwd=os.path.join(os.path.dirname(__file__), ".."),
        env=env,
        check=True,
        capture_output=True,
    )


# ── Docker bootstrap (module-load) ─────────────────────────────────────────
# Done at import time so SCRYER_TEST_DATABASE_URL is set before any scryer
# module is imported (and lru_cache-pins the prod URL).
_TEST_DB_URL = _start_docker_pg()
atexit.register(_stop_docker_pg)  # belt-and-suspenders; pytest_sessionfinish is preferred
try:
    _alembic_upgrade_head(_TEST_DB_URL)
except Exception:
    _stop_docker_pg()
    raise

# Now safe to import scryer modules — settings will pick up the env var.
from scryer.server.app import create_app  # noqa: E402
from scryer.server.db import get_session  # noqa: E402


def pytest_sessionfinish(session, exitstatus):  # type: ignore[no-untyped-def]
    """pytest hook: tear down docker postgres at end of session."""
    _stop_docker_pg()


# ── Engine / session fixtures ──────────────────────────────────────────────


@pytest_asyncio.fixture(scope="session")
async def engine() -> AsyncIterator[AsyncEngine]:
    """One engine per pytest session, against the migrated docker postgres."""
    eng = create_async_engine(_TEST_DB_URL, echo=False)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Per-test transactional session. All writes are rolled back on teardown.

    `join_transaction_mode="create_savepoint"` makes `session.commit()` calls
    inside business logic emit RELEASE SAVEPOINT instead of COMMIT, so the
    outer transaction stays open for rollback."""
    async with engine.connect() as conn:
        await conn.begin()
        await conn.begin_nested()

        async_session = AsyncSession(
            bind=conn,
            join_transaction_mode="create_savepoint",
        )

        @event.listens_for(async_session.sync_session, "after_transaction_end")
        def _reopen_savepoint(_sess, _transaction):  # type: ignore[no-untyped-def]
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
async def http_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Independent session for HTTP integration tests. Real commits to the
    test DB; isolated by being a fresh docker container per pytest session."""
    async with AsyncSession(bind=engine, expire_on_commit=False) as s:
        yield s


@pytest_asyncio.fixture
async def client(engine: AsyncEngine, http_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """ASGI test client. Each request gets its own session against the test
    engine. Resets the in-process rate limiter per test so unrelated tests
    don't blow each other's IP cap from the shared `testclient` host."""
    from scryer.server.services.rate_limit import _reset_for_tests

    _reset_for_tests()
    app = create_app()

    async def _override() -> AsyncIterator[AsyncSession]:
        async with AsyncSession(bind=engine, expire_on_commit=False) as s:
            yield s

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_session, None)


# ── RLS workspace context helper ───────────────────────────────────────────
# Tests that touch RLS-policied tables wrap their assertion blocks in:
#     async with workspace_context(session, ws.id):
#         await session.execute(select(...))
#
# Today (Phase 1c not yet landed) RLS is NOT enforced — this is a no-op.
# Once Phase 1c enables policies, the SET LOCAL value gates row visibility.
# Wrapping now means tests don't need to be re-touched when Phase 1c lands.


@asynccontextmanager
async def workspace_context(sess: AsyncSession, workspace_id: uuid.UUID) -> AsyncIterator[None]:
    """Set RLS workspace context for the next statement(s) in this session.
    Equivalent to setting session.info["workspace_id"] then triggering the
    RLS listener; this helper is convenient when you don't want to touch
    session.info directly."""
    sess.info["workspace_id"] = workspace_id
    yield
