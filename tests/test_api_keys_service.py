"""Unit tests for api_keys service.

API keys are Cat 4 (nullable workspace_id): user-keyed = NULL, SA-keyed = ws.
For user-keyed assertions there's no workspace to wrap with — those tests
will need rework once Phase 1c lands and the RLS policy on api_keys settles."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.models.enums import ApiScope, PrincipalKind
from scryer.server.services.api_keys import (
    issue_api_key,
    list_api_keys_for_principal,
    resolve_api_key,
    revoke_api_key,
)
from scryer.server.services.errors import AuthError
from scryer.server.services.service_accounts import create_service_account
from scryer.server.services.users import create_user
from scryer.server.services.workspaces import create_workspace
from tests.conftest import workspace_context


async def _user(session: AsyncSession):
    return await create_user(session, email=f"u{uuid4().hex[:8]}@example.com", password="x" * 16)


async def _ws(session: AsyncSession, owner_id):
    return await create_workspace(
        session, slug=f"ws-{uuid4().hex[:8]}", name="W", owner_user_id=owner_id
    )


async def test_issue_api_key_for_user(session: AsyncSession) -> None:
    # User-keyed api_key: workspace_id is NULL. No ws to wrap with.
    user = await _user(session)
    row, full = await issue_api_key(
        session,
        principal_kind=PrincipalKind.user,
        principal_id=user.id,
        scopes=[ApiScope.read, ApiScope.write],
        name="my-key",
    )
    assert full.startswith("scrk_live_")
    assert row.principal_user_id == user.id
    assert row.principal_service_account_id is None
    assert row.key_hash == hashlib.sha256(full.encode()).hexdigest()
    assert ApiScope.read in row.scopes


async def test_issue_api_key_for_service_account(session: AsyncSession) -> None:
    user = await _user(session)
    ws = await _ws(session, user.id)
    sa = await create_service_account(session, workspace_id=ws.id, name="bot")
    async with workspace_context(session, ws.id):
        row, full = await issue_api_key(
            session,
            principal_kind=PrincipalKind.service_account,
            principal_id=sa.id,
            scopes=[ApiScope.read, ApiScope.write],
        )
        assert row.principal_service_account_id == sa.id
        assert row.principal_user_id is None
        assert full.startswith("scrk_live_")


async def test_issue_api_key_service_account_admin_rejected(session: AsyncSession) -> None:
    """Plan §6: ServiceAccount keys cannot carry admin scope."""
    from scryer.server.services.errors import ValidationError

    user = await _user(session)
    ws = await _ws(session, user.id)
    sa = await create_service_account(session, workspace_id=ws.id, name="bot")
    async with workspace_context(session, ws.id):
        with pytest.raises(ValidationError):
            await issue_api_key(
                session,
                principal_kind=PrincipalKind.service_account,
                principal_id=sa.id,
                scopes=[ApiScope.admin],
            )


async def test_resolve_api_key_happy_path(session: AsyncSession) -> None:
    # User-keyed api_key — no workspace to wrap.
    user = await _user(session)
    row, full = await issue_api_key(
        session,
        principal_kind=PrincipalKind.user,
        principal_id=user.id,
        scopes=[ApiScope.read],
    )
    assert row.last_used_at is None
    resolved = await resolve_api_key(session, full)
    assert resolved.id == row.id
    assert resolved.last_used_at is not None


async def test_resolve_api_key_wrong_key_raises(session: AsyncSession) -> None:
    user = await _user(session)
    await issue_api_key(
        session,
        principal_kind=PrincipalKind.user,
        principal_id=user.id,
        scopes=[ApiScope.read],
    )
    with pytest.raises(AuthError):
        await resolve_api_key(session, "scrk_live_nope_nope_nope_nope")


async def test_resolve_api_key_revoked_raises(session: AsyncSession) -> None:
    user = await _user(session)
    row, full = await issue_api_key(
        session,
        principal_kind=PrincipalKind.user,
        principal_id=user.id,
        scopes=[ApiScope.read],
    )
    await revoke_api_key(session, row.id)
    with pytest.raises(AuthError):
        await resolve_api_key(session, full)


async def test_resolve_api_key_expired_raises(session: AsyncSession) -> None:
    user = await _user(session)
    row, full = await issue_api_key(
        session,
        principal_kind=PrincipalKind.user,
        principal_id=user.id,
        scopes=[ApiScope.read],
        expires_at=datetime.now(UTC) - timedelta(seconds=5),
    )
    with pytest.raises(AuthError):
        await resolve_api_key(session, full)


async def test_revoke_api_key_idempotent(session: AsyncSession) -> None:
    user = await _user(session)
    row, _ = await issue_api_key(
        session,
        principal_kind=PrincipalKind.user,
        principal_id=user.id,
        scopes=[ApiScope.read],
    )
    once = await revoke_api_key(session, row.id)
    first_ts = once.revoked_at
    twice = await revoke_api_key(session, row.id)
    assert twice.revoked_at == first_ts


async def test_list_api_keys_for_principal(session: AsyncSession) -> None:
    user = await _user(session)
    other = await _user(session)
    a, _ = await issue_api_key(
        session, principal_kind=PrincipalKind.user, principal_id=user.id, scopes=[ApiScope.read]
    )
    b, _ = await issue_api_key(
        session, principal_kind=PrincipalKind.user, principal_id=user.id, scopes=[ApiScope.read]
    )
    await issue_api_key(
        session, principal_kind=PrincipalKind.user, principal_id=other.id, scopes=[ApiScope.read]
    )
    listed = await list_api_keys_for_principal(
        session, principal_kind=PrincipalKind.user, principal_id=user.id
    )
    ids = {k.id for k in listed}
    assert {a.id, b.id} <= ids
