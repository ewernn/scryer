"""Tool service."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.models.eval import Tool
from scryer.server.services._versioned import (
    content_hash,
    get_latest,
    get_version,
    list_latest_per_slug,
    next_version,
)
from scryer.server.services.errors import NotFoundError


async def push_tool(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    slug: str,
    name: str,
    source_text: str,
    description: str | None = None,
    schema_json: dict[str, Any] | None = None,
    sandbox_required: bool = False,
) -> Tool:
    version, parent_id = await next_version(session, Tool, project_id=project_id, slug=slug)
    # FROZEN CONTRACT (Tool.content_hash): source_text + schema_json +
    # sandbox_required. Schema defines callable signature; sandbox_required
    # is intrinsic security property. Lock-in: tests/test_content_hash_stability.py.
    h = content_hash(
        {
            "source_text": source_text,
            "schema_json": schema_json,
            "sandbox_required": sandbox_required,
        }
    )
    t = Tool(
        project_id=project_id,
        slug=slug,
        version=version,
        content_hash=h,
        parent_id=parent_id,
        name=name,
        description=description,
        source_text=source_text,
        schema_json=schema_json,
        sandbox_required=sandbox_required,
    )
    session.add(t)
    await session.flush()
    return t


async def get_tool(session: AsyncSession, tool_id: uuid.UUID) -> Tool:
    t = await session.get(Tool, tool_id)
    if t is None or t.archived_at is not None:
        raise NotFoundError("tool", str(tool_id))
    return t


async def list_tools(session: AsyncSession, *, project_id: uuid.UUID) -> list[Tool]:
    return await list_latest_per_slug(session, Tool, project_id=project_id)


async def get_tool_latest(session: AsyncSession, *, project_id: uuid.UUID, slug: str) -> Tool:
    return await get_latest(session, Tool, project_id=project_id, slug=slug)


async def get_tool_version(
    session: AsyncSession, *, project_id: uuid.UUID, slug: str, version: int
) -> Tool:
    return await get_version(session, Tool, project_id=project_id, slug=slug, version=version)


async def archive_tool(session: AsyncSession, tool_id: uuid.UUID) -> Tool:
    t = await get_tool(session, tool_id)
    t.archived_at = datetime.now(UTC)
    await session.flush()
    return t
