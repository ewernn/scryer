"""Idempotency-Key middleware as a FastAPI dependency.

Stripe-style at-most-once retry safety. The dep runs AFTER `get_principal`
+ `require_workspace_from_path` (or `set_user_context_dep` for non-ws
routes), so RLS + principal context are already established when the
key lookup happens. ON CONFLICT DO NOTHING claims the slot atomically;
on conflict, fingerprint-validate the existing row and raise either a
cached-response signal or a 422/409.

Stripe semantics:
- Header absent: silent no-op (route runs as if no idempotency).
- Same key, same body, prior 4xx/2xx response: return cached.
- Same key, same body, prior in-flight (status_code=NULL): 409.
- Same key, DIFFERENT body: 422 (key reuse with mismatched request).
- 5xx responses are NOT cached — let retries proceed.
- 24h TTL (env-configurable).
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import BackgroundTasks, Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from scryer.server.auth import Principal, get_principal
from scryer.server.db import get_session
from scryer.server.services.errors import ConflictError, ValidationError

_TTL_SECONDS = int(os.environ.get("IDEMPOTENCY_TTL_SECONDS", str(24 * 3600)))


class CachedResponseError(Exception):
    """Signal raised when the dep finds a cached response. The exception
    handler catches it and returns the stored body verbatim."""

    def __init__(self, status_code: int, body: dict[str, Any] | None) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"idempotency cache hit, status={status_code}")


def _canonical_hash(body: bytes) -> str:
    """sha256 of canonical JSON if parseable; otherwise raw body bytes.

    Multipart/binary bodies fall through to the raw-bytes path. Clients
    sending {"a": 1, "b": 2} and {"b": 2, "a": 1} produce the same hash."""
    if not body:
        return hashlib.sha256(b"").hexdigest()
    try:
        parsed = json.loads(body)
        canonical = json.dumps(parsed, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    except (ValueError, UnicodeDecodeError):
        return hashlib.sha256(body).hexdigest()


async def check_idempotency(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
) -> None:
    """FastAPI dep. Silent no-op when Idempotency-Key header is absent.

    Side-effects when the header is present:
    - Cached prior response → raises CachedResponseError (handler returns it).
    - Body fingerprint mismatch → raises ValidationError (422).
    - Slot in-flight (status_code IS NULL) → raises ConflictError (409).
    - Fresh slot → INSERT placeholder, stash row id on request.state.
    """
    raw_key = request.headers.get("Idempotency-Key")
    if not raw_key:
        return
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return

    body = await request.body()
    # Cache the body bytes so the handler can re-parse via Request.json().
    request.state.cached_body = body
    req_hash = _canonical_hash(body)

    workspace_id: uuid.UUID | None = getattr(request.state, "workspace_id", None)
    expires_at = datetime.now(UTC) + timedelta(seconds=_TTL_SECONDS)

    insert_sql = text(
        """
        INSERT INTO idempotency_keys
            (workspace_id, principal_id, key, request_hash, created_at, expires_at)
        VALUES
            (:ws, :pid, :key, :hash, now(), :exp)
        ON CONFLICT DO NOTHING
        RETURNING id
        """
    )
    result = await session.execute(
        insert_sql,
        {
            "ws": workspace_id,
            "pid": principal.id,
            "key": raw_key,
            "hash": req_hash,
            "exp": expires_at,
        },
    )
    row = result.fetchone()

    if row is not None:
        # Fresh slot — handler runs, response capture writes back via BackgroundTask.
        request.state.idempotency_row_id = row.id
        return

    # Conflict — fetch the existing row. CAST(:ws AS uuid) instead of
    # :ws::uuid because SQLAlchemy's text() param parser doesn't recognise
    # :ws when immediately followed by ::.
    select_sql = text(
        """
        SELECT id, status_code, response_body, request_hash
        FROM idempotency_keys
        WHERE principal_id = :pid AND key = :key
          AND ((CAST(:ws AS uuid) IS NULL AND workspace_id IS NULL)
               OR workspace_id = CAST(:ws AS uuid))
        LIMIT 1
        """
    )
    existing = (
        await session.execute(select_sql, {"pid": principal.id, "key": raw_key, "ws": workspace_id})
    ).fetchone()

    if existing is None:
        # Race: row was swept between INSERT and SELECT (e.g. TTL expiry +
        # cleanup). Treat as fresh; handler runs without idempotency capture.
        return

    if existing.request_hash != req_hash:
        raise ValidationError(
            f"Idempotency-Key {raw_key!r} was used with a different request body."
        )

    if existing.status_code is None:
        raise ConflictError(f"Idempotency-Key {raw_key!r} is already processing (in-flight).")

    # Cached prior response.
    raise CachedResponseError(existing.status_code, existing.response_body)


def capture_idempotency_response(
    background_tasks: BackgroundTasks,
    request: Request,
    session_factory: async_sessionmaker[AsyncSession] | None,
    *,
    status_code: int,
    body: dict[str, Any] | None,
) -> None:
    """Schedule the post-response write that caches status+body.

    Skips if no idempotency row was claimed (header absent OR cache hit
    short-circuited). 5xx responses aren't cached — retries should re-execute.
    Skips if the app's session_factory isn't initialised (e.g. starlette
    TestClient running without lifespan)."""
    row_id: uuid.UUID | None = getattr(request.state, "idempotency_row_id", None)
    if row_id is None:
        return
    if status_code >= 500:
        return
    if session_factory is None:
        return

    async def _write() -> None:
        async with session_factory() as s:
            await s.execute(
                text(
                    "UPDATE idempotency_keys "
                    "SET status_code = :sc, response_body = :body "
                    "WHERE id = :id"
                ),
                {"sc": status_code, "body": json.dumps(body) if body else None, "id": row_id},
            )
            await s.commit()

    background_tasks.add_task(_write)


def with_idempotency_capture(
    session_factory_attr: str = "session_factory",
) -> Callable[..., None]:
    """Convenience: build a Depends that returns a callable for the handler
    to invoke at response time. (Currently unused — handlers invoke
    capture_idempotency_response directly with explicit status/body.)"""
    raise NotImplementedError(
        "Reserved for a future cleaner pattern; use capture_idempotency_response directly."
    )
