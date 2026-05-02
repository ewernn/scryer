"""Credential service: store/retrieve encrypted external LLM API keys.

Plaintext is encrypted at write via AES-GCM using the env's ENCRYPTION_KEY.
Decrypt happens at use-time only — never round-trip plaintext to the caller.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.models.auth import Credential
from scryer.server.models.enums import CredentialProvider
from scryer.server.services.errors import NotFoundError, PermissionError
from scryer.server.services.security import decrypt_credential, encrypt_credential


async def add_credential(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    name: str,
    provider: CredentialProvider,
    plaintext_value: str,
    metadata_json: dict[str, Any] | None = None,
    restricted_to_user_ids: list[uuid.UUID] | None = None,
) -> Credential:
    """Store a Credential. AAD binds the ciphertext to (id, workspace_id) so
    swapping blobs across rows fails decryption."""
    cred = Credential(
        workspace_id=workspace_id,
        name=name,
        provider=provider,
        encrypted_value="",
        encryption_key_version=0,
        metadata_json=metadata_json,
        restricted_to_user_ids=restricted_to_user_ids,
    )
    session.add(cred)
    await session.flush()  # populates cred.id

    aad = _aad_for(cred.id, workspace_id)
    encrypted, version = encrypt_credential(plaintext_value, aad=aad)
    cred.encrypted_value = encrypted
    cred.encryption_key_version = version
    await session.flush()
    return cred


async def get_credential(session: AsyncSession, cred_id: uuid.UUID) -> Credential:
    cred = await session.get(Credential, cred_id)
    if cred is None or cred.archived_at is not None:
        raise NotFoundError("credential", str(cred_id))
    return cred


async def list_credentials(session: AsyncSession, workspace_id: uuid.UUID) -> list[Credential]:
    """List Credentials in a Workspace. Plaintext NOT included."""
    stmt = (
        select(Credential)
        .where(
            Credential.workspace_id == workspace_id,
            Credential.archived_at.is_(None),
        )
        .order_by(Credential.created_at.desc())
    )
    result = await session.execute(stmt)
    return list(result.scalars())


async def decrypt_for_user(session: AsyncSession, *, cred_id: uuid.UUID, user_id: uuid.UUID) -> str:
    """Decrypt with row-bound AAD; blob swap across rows fails decryption."""
    cred = await get_credential(session, cred_id)
    if cred.restricted_to_user_ids is not None and user_id not in cred.restricted_to_user_ids:
        raise PermissionError(f"User {user_id} not authorized to use credential {cred.name!r}")
    aad = _aad_for(cred.id, cred.workspace_id)
    return decrypt_credential(cred.encrypted_value, cred.encryption_key_version, aad=aad)


def _aad_for(cred_id: uuid.UUID, workspace_id: uuid.UUID) -> bytes:
    return f"cred:{cred_id}|ws:{workspace_id}".encode()


async def archive_credential(session: AsyncSession, cred_id: uuid.UUID) -> Credential:
    cred = await get_credential(session, cred_id)
    cred.archived_at = datetime.now(UTC)
    await session.flush()
    return cred
