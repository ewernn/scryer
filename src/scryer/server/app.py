"""FastAPI application factory.

Wires Sentry (no-op if SENTRY_DSN unset; FastAPI integration auto-attaches),
attaches DB engine to app.state via lifespan, mounts /api/v1 router. /mcp and
dashboard routes land in later phases.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import APIRouter, FastAPI

from scryer import __version__
from scryer.config import get_settings
from scryer.server.api.healthz import router as healthz_router
from scryer.server.db import build_engine, build_session_factory


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    engine = build_engine(settings.database_url)
    app.state.engine = engine
    app.state.session_factory = build_session_factory(engine)
    try:
        yield
    finally:
        await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()

    if settings.sentry_dsn:
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.env,
            release=f"scryer@{__version__}",
            traces_sample_rate=settings.sentry_traces_sample_rate,
        )

    app = FastAPI(
        title="scryer",
        version=__version__,
        description="LLM evaluation harness with control-plane primitives.",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    api_v1 = APIRouter(prefix="/api/v1")
    api_v1.include_router(healthz_router)
    app.include_router(api_v1)

    return app


app = create_app()
