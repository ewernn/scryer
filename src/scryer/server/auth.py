"""Auth dependency: resolves a Bearer token to a Principal.

Single HTTPBearer dep dispatches by prefix:
- `scrk_live_*` → ApiKey path
- contains `.` → JWT path
- else → 401

`require_scope("write")` factory wraps `get_principal` for scope enforcement.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.db import get_session
from scryer.server.models.enums import ApiScope, PrincipalKind
from scryer.server.services.api_keys import resolve_api_key
from scryer.server.services.errors import AuthError, PermissionError
from scryer.server.services.security import verify_access_jwt

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    id: uuid.UUID
    kind: PrincipalKind
    scopes: frozenset[ApiScope]
    api_key_id: uuid.UUID | None = None


async def get_principal(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Principal:
    if creds is None:
        raise AuthError("Missing credentials")
    token = creds.credentials
    if token.startswith("scrk_live_"):
        return await _principal_from_api_key(token, session)
    if "." in token:
        return _principal_from_jwt(token)
    raise AuthError("Unrecognized credential format")


async def _principal_from_api_key(token: str, session: AsyncSession) -> Principal:
    row = await resolve_api_key(session, token)  # raises AuthError on failure
    pid = (
        row.principal_user_id
        if row.principal_kind == PrincipalKind.user
        else row.principal_service_account_id
    )
    assert pid is not None  # CHECK constraint guarantees this
    return Principal(
        id=pid, kind=row.principal_kind, scopes=frozenset(row.scopes), api_key_id=row.id
    )


def _principal_from_jwt(token: str) -> Principal:
    try:
        claims = verify_access_jwt(token)
    except jwt.PyJWTError as exc:
        raise AuthError(str(exc)) from exc
    sub = claims.get("sub")
    if not sub:
        raise AuthError("JWT missing sub claim")
    try:
        sub_uuid = uuid.UUID(sub)
    except ValueError as exc:
        raise AuthError("JWT sub is not a valid UUID") from exc
    return Principal(
        id=sub_uuid,
        kind=PrincipalKind.user,
        scopes=frozenset({ApiScope.read, ApiScope.write, ApiScope.admin}),
    )


def require_scope(scope: ApiScope) -> Callable[[Principal], Awaitable[Principal]]:
    async def _dep(
        principal: Annotated[Principal, Depends(get_principal)],
    ) -> Principal:
        if scope not in principal.scopes:
            raise PermissionError(f"Scope required: {scope.value}")
        return principal

    return _dep
