"""ServiceAccount service: create bot identities; toggle active flag."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.models.auth import ServiceAccount
from scryer.server.services.errors import NotFoundError


async def create_service_account(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    name: str,
    owner_user_id: uuid.UUID | None = None,
    requires_approval: bool = False,
) -> ServiceAccount:
    sa = ServiceAccount(
        workspace_id=workspace_id,
        name=name,
        owner_user_id=owner_user_id,
        requires_approval=requires_approval,
    )
    session.add(sa)
    await session.flush()
    return sa


async def get_service_account(session: AsyncSession, sa_id: uuid.UUID) -> ServiceAccount:
    sa = await session.get(ServiceAccount, sa_id)
    if sa is None or sa.archived_at is not None:
        raise NotFoundError("service_account", str(sa_id))
    return sa


async def list_service_accounts(
    session: AsyncSession, workspace_id: uuid.UUID
) -> list[ServiceAccount]:
    stmt = (
        select(ServiceAccount)
        .where(
            ServiceAccount.workspace_id == workspace_id,
            ServiceAccount.archived_at.is_(None),
        )
        .order_by(ServiceAccount.created_at.desc())
    )
    result = await session.execute(stmt)
    return list(result.scalars())


async def set_active(session: AsyncSession, sa_id: uuid.UUID, active: bool) -> ServiceAccount:
    sa = await get_service_account(session, sa_id)
    sa.is_active = active
    await session.flush()
    return sa


async def archive_service_account(session: AsyncSession, sa_id: uuid.UUID) -> ServiceAccount:
    sa = await get_service_account(session, sa_id)
    sa.archived_at = datetime.now(UTC)
    await session.flush()
    return sa
