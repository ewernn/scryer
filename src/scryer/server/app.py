"""FastAPI application factory.

Wires Sentry (no-op if SENTRY_DSN unset), mounts /api/v1 router, leaves
/mcp and dashboard routes for later phases.
"""

from __future__ import annotations

import sentry_sdk
from fastapi import APIRouter, FastAPI
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware

from scryer import __version__
from scryer.config import get_settings
from scryer.server.api.healthz import router as healthz_router


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
    )

    if settings.sentry_dsn:
        app.add_middleware(SentryAsgiMiddleware)

    api_v1 = APIRouter(prefix="/api/v1")
    api_v1.include_router(healthz_router)
    app.include_router(api_v1)

    return app


app = create_app()
