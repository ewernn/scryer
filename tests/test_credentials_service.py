"""Unit tests for credentials service."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.models.enums import CredentialProvider
from scryer.server.services.credentials import (
    add_credential,
    archive_credential,
    decrypt_for_user,
    list_credentials,
)
from scryer.server.services.errors import PermissionError
from scryer.server.services.users import create_user
from scryer.server.services.workspaces import create_workspace
from tests.conftest import workspace_context


async def _user(session: AsyncSession):
    return await create_user(session, email=f"u{uuid4().hex[:8]}@example.com", password="x" * 16)


async def _ws(session: AsyncSession, owner_id):
    return await create_workspace(
        session, slug=f"ws-{uuid4().hex[:8]}", name="W", owner_user_id=owner_id
    )


async def test_add_credential_round_trips(session: AsyncSession) -> None:
    owner = await _user(session)
    ws = await _ws(session, owner.id)
    plaintext = "sk-very-secret-1234567890"
    cred = await add_credential(
        session,
        workspace_id=ws.id,
        name="openai-prod",
        provider=CredentialProvider.openai,
        plaintext_value=plaintext,
    )
    assert cred.encrypted_value != plaintext
    async with workspace_context(session, ws.id):
        decrypted = await decrypt_for_user(session, cred_id=cred.id, user_id=owner.id)
        assert decrypted == plaintext


async def test_decrypt_restricted_users_enforces(session: AsyncSession) -> None:
    owner = await _user(session)
    allowed = await _user(session)
    other = await _user(session)
    ws = await _ws(session, owner.id)
    cred = await add_credential(
        session,
        workspace_id=ws.id,
        name="restricted",
        provider=CredentialProvider.anthropic,
        plaintext_value="sk-ant-secret",
        restricted_to_user_ids=[allowed.id],
    )
    async with workspace_context(session, ws.id):
        pt = await decrypt_for_user(session, cred_id=cred.id, user_id=allowed.id)
        assert pt == "sk-ant-secret"
        with pytest.raises(PermissionError):
            await decrypt_for_user(session, cred_id=cred.id, user_id=other.id)


async def test_archive_credential_filters_out(session: AsyncSession) -> None:
    owner = await _user(session)
    ws = await _ws(session, owner.id)
    cred = await add_credential(
        session,
        workspace_id=ws.id,
        name="goner",
        provider=CredentialProvider.openai,
        plaintext_value="sk-x",
    )
    await archive_credential(session, cred.id)
    async with workspace_context(session, ws.id):
        remaining = await list_credentials(session, ws.id)
        assert all(c.id != cred.id for c in remaining)
