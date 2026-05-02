"""ApiKey service: issue, verify, revoke, usage logging."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.models.auth import ApiKey, ApiKeyUsage
from scryer.server.models.enums import ApiScope, PrincipalKind
from scryer.server.services.errors import AuthError, NotFoundError, ValidationError
from scryer.server.services.security import (
    generate_api_key,
    verify_api_key,
)


async def issue_api_key(
    session: AsyncSession,
    *,
    principal_kind: PrincipalKind,
    principal_id: uuid.UUID,
    scopes: list[ApiScope],
    name: str | None = None,
    expires_at: datetime | None = None,
) -> tuple[ApiKey, str]:
    """Returns (db row, full_key shown once). full_key never persisted."""
    if principal_kind == PrincipalKind.service_account and ApiScope.admin in scopes:
        raise ValidationError("ServiceAccount keys cannot carry 'admin' scope (plan §6)")
    full_key, key_prefix, key_hash = generate_api_key()

    row = ApiKey(
        principal_kind=principal_kind,
        principal_user_id=principal_id if principal_kind == PrincipalKind.user else None,
        principal_service_account_id=(
            principal_id if principal_kind == PrincipalKind.service_account else None
        ),
        key_prefix=key_prefix,
        key_hash=key_hash,
        name=name,
        scopes=scopes,
        expires_at=expires_at,
    )
    session.add(row)
    await session.flush()
    return row, full_key


async def resolve_api_key(session: AsyncSession, full_key: str) -> ApiKey:
    """Look up an ApiKey row by full_key.

    - Looks up by `key_prefix` (indexed) → ~1 row
    - Verifies sha256(full_key) matches `key_hash` (constant-time compare)
    - Checks revoked_at, expires_at
    - Updates last_used_at (best-effort; errors don't block auth)

    Raises AuthError on any failure.
    """
    if len(full_key) < 16:
        raise AuthError("Invalid API key")
    key_prefix = full_key[:16]

    result = await session.execute(select(ApiKey).where(ApiKey.key_prefix == key_prefix))
    candidates = list(result.scalars())
    matched: ApiKey | None = None
    for cand in candidates:
        if verify_api_key(full_key, cand.key_hash):
            matched = cand
            break

    if matched is None:
        raise AuthError("Invalid API key")

    if matched.revoked_at is not None:
        raise AuthError("API key revoked")

    if matched.expires_at is not None and matched.expires_at < datetime.now(UTC):
        raise AuthError("API key expired")

    matched.last_used_at = datetime.now(UTC)
    return matched


async def revoke_api_key(session: AsyncSession, key_id: uuid.UUID) -> ApiKey:
    row = await session.get(ApiKey, key_id)
    if row is None:
        raise NotFoundError("api_key", str(key_id))
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        await session.flush()
    return row


async def log_api_key_usage(
    session: AsyncSession,
    *,
    api_key_id: uuid.UUID,
    timestamp: datetime,
    ip_address: str | None = None,
    endpoint: str | None = None,
    status_code: int | None = None,
) -> None:
    """Lightweight per-request log. Append-only; no UPDATEs."""
    row = ApiKeyUsage(
        api_key_id=api_key_id,
        timestamp=timestamp,
        ip_address=ip_address,
        endpoint=endpoint,
        status_code=status_code,
    )
    session.add(row)
    await session.flush()


async def list_api_keys_for_principal(
    session: AsyncSession,
    *,
    principal_kind: PrincipalKind,
    principal_id: uuid.UUID,
    include_revoked: bool = False,
) -> list[ApiKey]:
    if principal_kind == PrincipalKind.user:
        cond = ApiKey.principal_user_id == principal_id
    else:
        cond = ApiKey.principal_service_account_id == principal_id
    stmt = select(ApiKey).where(cond).order_by(ApiKey.created_at.desc())
    if not include_revoked:
        stmt = stmt.where(ApiKey.revoked_at.is_(None))
    result = await session.execute(stmt)
    return list(result.scalars())
