"""User service: signup, login, lookup. Side-effects on Workspace + Project
auto-creation per plan §5 happen in invitations.redeem (not here)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.models.auth import User
from scryer.server.services.errors import (
    AuthError,
    ConflictError,
    NotFoundError,
)
from scryer.server.services.security import (
    hash_password,
    password_needs_rehash,
    verify_password,
)


async def create_user(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    display_name: str | None = None,
) -> User:
    """Create a new User with argon2id hashed password.

    Raises ConflictError if active user with this email already exists.
    """
    existing = await session.execute(
        select(User).where(User.email == email, User.archived_at.is_(None))
    )
    if existing.scalar_one_or_none():
        raise ConflictError(f"Active user with email {email!r} already exists")

    user = User(
        email=email,
        password_hash=await hash_password(password),
        display_name=display_name,
    )
    session.add(user)
    await session.flush()  # populates user.id
    return user


async def get_user(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = await session.get(User, user_id)
    if user is None or user.archived_at is not None:
        raise NotFoundError("user", str(user_id))
    return user


async def get_user_by_email(session: AsyncSession, email: str) -> User:
    result = await session.execute(
        select(User).where(User.email == email, User.archived_at.is_(None))
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise NotFoundError("user", email)
    return user


async def authenticate(session: AsyncSession, *, email: str, password: str) -> User:
    """Verify email+password. Raises AuthError on bad creds.

    Lazy rehash: if password_needs_rehash, re-hashes with current params and
    persists. This is how OWASP-param upgrades roll forward without forcing
    user re-login.
    """
    try:
        user = await get_user_by_email(session, email)
    except NotFoundError as exc:
        raise AuthError("Invalid email or password") from exc

    if not await verify_password(user.password_hash, password):
        raise AuthError("Invalid email or password")

    if await password_needs_rehash(user.password_hash):
        user.password_hash = await hash_password(password)
        await session.flush()

    return user


async def archive_user(session: AsyncSession, user_id: uuid.UUID) -> User:
    """Soft-delete. Does NOT cascade to Workspaces/Projects (those use
    `ondelete='RESTRICT'` on the FK, so will block hard-deletes regardless)."""
    user = await get_user(session, user_id)
    user.archived_at = datetime.now(UTC)
    await session.flush()
    return user
