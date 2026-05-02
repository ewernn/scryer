"""GET /api/v1/healthz — liveness + DB ping."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from scryer import __version__
from scryer.server.db import get_engine

router = APIRouter()


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    db_ok: bool


@router.get("/healthz", response_model=HealthResponse, tags=["meta"])
async def healthz() -> HealthResponse:
    db_ok = True
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    return HealthResponse(
        status="ok" if db_ok else "degraded",
        version=__version__,
        db_ok=db_ok,
    )
