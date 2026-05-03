"""Agent service. Same shape as scorers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.models.eval import Agent
from scryer.server.services._versioned import (
    content_hash,
    get_latest,
    get_version,
    list_latest_per_slug,
    next_version,
)
from scryer.server.services.errors import NotFoundError


async def push_agent(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    slug: str,
    name: str,
    source_text: str,
    description: str | None = None,
    config_json: dict[str, Any] | None = None,
) -> Agent:
    version, parent_id = await next_version(session, Agent, project_id=project_id, slug=slug)
    # FROZEN CONTRACT (Agent.content_hash): source_text + config_json.
    # config_json is intrinsic — affects agent behavior (model, temperature).
    # Lock-in test: tests/test_content_hash_stability.py.
    h = content_hash({"source_text": source_text, "config_json": config_json})
    a = Agent(
        project_id=project_id,
        slug=slug,
        version=version,
        content_hash=h,
        parent_id=parent_id,
        name=name,
        description=description,
        source_text=source_text,
        config_json=config_json,
    )
    session.add(a)
    await session.flush()
    return a


async def get_agent(session: AsyncSession, agent_id: uuid.UUID) -> Agent:
    a = await session.get(Agent, agent_id)
    if a is None or a.archived_at is not None:
        raise NotFoundError("agent", str(agent_id))
    return a


async def get_agent_latest(session: AsyncSession, *, project_id: uuid.UUID, slug: str) -> Agent:
    return await get_latest(session, Agent, project_id=project_id, slug=slug)


async def get_agent_version(
    session: AsyncSession, *, project_id: uuid.UUID, slug: str, version: int
) -> Agent:
    return await get_version(session, Agent, project_id=project_id, slug=slug, version=version)


async def list_agents(session: AsyncSession, *, project_id: uuid.UUID) -> list[Agent]:
    return await list_latest_per_slug(session, Agent, project_id=project_id)


async def archive_agent(session: AsyncSession, agent_id: uuid.UUID) -> Agent:
    a = await get_agent(session, agent_id)
    a.archived_at = datetime.now(UTC)
    await session.flush()
    return a
