"""FastAPI application factory.

Wires Sentry (no-op if SENTRY_DSN unset; FastAPI integration auto-attaches),
attaches DB engine to app.state via lifespan, mounts /api/v1 router. /mcp and
dashboard routes land in later phases.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import sentry_sdk
from asgi_correlation_id import CorrelationIdMiddleware
from fastapi import APIRouter, FastAPI
from fastapi.exceptions import RequestValidationError

from scryer import __version__
from scryer.config import get_settings
from scryer.server.api.auth import router as auth_router
from scryer.server.api.datasets import router as datasets_router
from scryer.server.api.healthz import router as healthz_router
from scryer.server.api.projects import router as projects_router
from scryer.server.api.runs import router as runs_router
from scryer.server.api.scorers import router as scorers_router
from scryer.server.api.tasks import router as tasks_router
from scryer.server.api.workspaces import router as workspaces_router
from scryer.server.db import build_engine, build_session_factory
from scryer.server.exception_handlers import (
    request_validation_handler,
    scryer_error_handler,
)
from scryer.server.services.errors import ScryerError


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

    app.add_middleware(CorrelationIdMiddleware, header_name="X-Request-ID")
    app.add_exception_handler(ScryerError, scryer_error_handler)
    app.add_exception_handler(RequestValidationError, request_validation_handler)

    api_v1 = APIRouter(prefix="/api/v1")
    api_v1.include_router(healthz_router)
    api_v1.include_router(auth_router)
    api_v1.include_router(workspaces_router)
    api_v1.include_router(projects_router)
    api_v1.include_router(datasets_router)
    api_v1.include_router(scorers_router)
    api_v1.include_router(tasks_router)
    api_v1.include_router(runs_router)
    app.include_router(api_v1)

    return app


app = create_app()
