"""Unit tests for users service."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.services.errors import AuthError, ConflictError
from scryer.server.services.security import hash_password, password_needs_rehash
from scryer.server.services.users import (
    archive_user,
    authenticate,
    create_user,
)


async def test_create_user_happy_path(session: AsyncSession) -> None:
    email = f"u{uuid4().hex[:8]}@example.com"
    user = await create_user(session, email=email, password="hunter2hunter2", display_name="Alice")
    assert user.id is not None
    assert user.email == email
    assert user.password_hash and user.password_hash != "hunter2hunter2"
    assert user.display_name == "Alice"


async def test_create_user_duplicate_email_conflict(session: AsyncSession) -> None:
    email = f"u{uuid4().hex[:8]}@example.com"
    await create_user(session, email=email, password="pw-one-very-long")
    with pytest.raises(ConflictError):
        await create_user(session, email=email, password="pw-two-very-long")


async def test_create_user_soft_deleted_email_succeeds(session: AsyncSession) -> None:
    email = f"u{uuid4().hex[:8]}@example.com"
    first = await create_user(session, email=email, password="first-password-x")
    await archive_user(session, first.id)
    again = await create_user(session, email=email, password="second-password-x")
    assert again.id != first.id
    assert again.email == email


async def test_authenticate_happy_path(session: AsyncSession) -> None:
    email = f"u{uuid4().hex[:8]}@example.com"
    await create_user(session, email=email, password="correct-horse-batt")
    user = await authenticate(session, email=email, password="correct-horse-batt")
    assert user.email == email


async def test_authenticate_wrong_password_raises(session: AsyncSession) -> None:
    email = f"u{uuid4().hex[:8]}@example.com"
    await create_user(session, email=email, password="correct-horse-batt")
    with pytest.raises(AuthError):
        await authenticate(session, email=email, password="wrong-horse-batt")


async def test_authenticate_unknown_email_raises(session: AsyncSession) -> None:
    with pytest.raises(AuthError):
        await authenticate(
            session, email=f"nope{uuid4().hex[:8]}@example.com", password="anything-here-xx"
        )


async def test_password_needs_rehash_flow(session: AsyncSession) -> None:
    h = await hash_password("a-strong-pass-1")
    assert await password_needs_rehash(h) is False
