"""GET /api/v1/healthz — liveness + DB ping. /healthz/deep adds the rest."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel
from sqlalchemy import text

from scryer import __version__
from scryer.config import get_settings

router = APIRouter()


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    db_ok: bool


@router.get("/healthz", response_model=HealthResponse, tags=["meta"])
async def healthz(request: Request) -> HealthResponse:
    db_ok = True
    try:
        async with request.app.state.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    return HealthResponse(
        status="ok" if db_ok else "degraded",
        version=__version__,
        db_ok=db_ok,
    )


# Expected migration head — bumping this manually in lock-step with new
# alembic revisions catches "shipped code without running alembic upgrade".
# Future improvement: read this from alembic config dynamically.
EXPECTED_MIGRATION_HEAD = "c557d6a52788"

# Stale-run threshold: a Run is "stuck" if it claims to be running but its
# heartbeat is older than this. Matches the reaper's grace period.
STALE_RUN_GRACE_SECONDS = 90


class CheckResult(BaseModel):
    ok: bool
    detail: str | None = None


class DeepHealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    db: CheckResult
    migration: CheckResult
    r2: CheckResult
    webhook_queue_depth: CheckResult
    stale_runs: CheckResult


@router.get("/healthz/deep", response_model=DeepHealthResponse, tags=["meta"])
async def healthz_deep(request: Request, response: Response) -> DeepHealthResponse:
    """Multi-check health probe for ops dashboards / oncall pages.

    Each sub-check returns ok=False with a detail message if degraded.
    Top-level status is "degraded" iff any sub-check fails. The endpoint
    sets HTTP 503 in the degraded case so probes can react via status
    code alone, not body parsing."""
    db_check = CheckResult(ok=True)
    migration_check = CheckResult(ok=True)
    r2_check = CheckResult(ok=True)
    queue_check = CheckResult(ok=True)
    stale_check = CheckResult(ok=True)

    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        db_check = CheckResult(ok=False, detail="app.state.engine not initialised")

    # 1. DB connectivity (same as /healthz).
    if engine is not None:
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:
            db_check = CheckResult(ok=False, detail=f"db connect failed: {type(exc).__name__}")

    # Bail early on subsequent DB-dependent checks if connect failed.
    if db_check.ok and engine is not None:
        try:
            async with engine.connect() as conn:
                head = await conn.execute(text("SELECT version_num FROM alembic_version"))
                row = head.scalar_one_or_none()
                if row != EXPECTED_MIGRATION_HEAD:
                    migration_check = CheckResult(
                        ok=False,
                        detail=f"alembic head is {row!r}, expected {EXPECTED_MIGRATION_HEAD!r}",
                    )

                # webhook_deliveries + runs are RLS-policied; iterate per
                # workspace and aggregate. workspaces table itself is not
                # RLS-policied so the listing is allowed without GUC.
                # Same pattern as api/internal.py cron paths.
                ws_rows = await conn.execute(
                    text("SELECT id FROM workspaces WHERE archived_at IS NULL")
                )
                ws_ids = [r[0] for r in ws_rows.all()]

                cutoff = datetime.now(UTC) - timedelta(seconds=STALE_RUN_GRACE_SECONDS)
                n_pending = 0
                n_stale = 0
                for ws_id in ws_ids:
                    await conn.execute(
                        text("SELECT set_config('app.current_workspace_id', :w, true)"),
                        {"w": str(ws_id)},
                    )
                    pending = await conn.execute(
                        text(
                            "SELECT count(*) FROM webhook_deliveries "
                            "WHERE status IN ('pending', 'failed') AND next_attempt_at < now()"
                        )
                    )
                    n_pending += pending.scalar_one()
                    stale = await conn.execute(
                        text(
                            "SELECT count(*) FROM runs "
                            "WHERE status = 'running' AND last_heartbeat_at < :cutoff"
                        ),
                        {"cutoff": cutoff},
                    )
                    n_stale += stale.scalar_one()

                if n_pending > 100:
                    queue_check = CheckResult(
                        ok=False, detail=f"{n_pending} pending webhook deliveries past due"
                    )
                if n_stale > 0:
                    stale_check = CheckResult(
                        ok=False,
                        detail=f"{n_stale} runs stuck > {STALE_RUN_GRACE_SECONDS}s without heartbeat",
                    )
        except Exception as exc:
            migration_check = CheckResult(
                ok=False, detail=f"migration check failed: {type(exc).__name__}: {exc}"
            )

    # 2. R2 connectivity. Cheap head_bucket call.
    settings = get_settings()
    if not (settings.r2_endpoint and settings.r2_access_key_id):
        r2_check = CheckResult(ok=False, detail="R2 credentials not configured")
    else:
        try:
            from scryer.server.services.blobs import _get_client

            await asyncio.to_thread(_get_client().head_bucket, Bucket=settings.r2_bucket_name)
        except Exception as exc:
            r2_check = CheckResult(ok=False, detail=f"R2 head_bucket failed: {type(exc).__name__}")

    overall_ok = all(c.ok for c in (db_check, migration_check, r2_check, queue_check, stale_check))
    if not overall_ok:
        response.status_code = 503

    return DeepHealthResponse(
        status="ok" if overall_ok else "degraded",
        version=__version__,
        db=db_check,
        migration=migration_check,
        r2=r2_check,
        webhook_queue_depth=queue_check,
        stale_runs=stale_check,
    )
