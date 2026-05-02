"""Prompt service."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.models.enums import PromptTemplateFormat
from scryer.server.models.eval import Prompt
from scryer.server.services._versioned import (
    content_hash,
    get_latest,
    get_version,
    list_latest_per_slug,
    next_version,
)
from scryer.server.services.errors import NotFoundError


async def push_prompt(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    slug: str,
    name: str,
    template: str,
    template_format: PromptTemplateFormat = PromptTemplateFormat.fstring,
    description: str | None = None,
) -> Prompt:
    version, parent_id = await next_version(session, Prompt, project_id=project_id, slug=slug)
    h = content_hash({"template": template, "template_format": template_format.value})
    p = Prompt(
        project_id=project_id,
        slug=slug,
        version=version,
        content_hash=h,
        parent_id=parent_id,
        name=name,
        description=description,
        template=template,
        template_format=template_format,
    )
    session.add(p)
    await session.flush()
    return p


async def get_prompt(session: AsyncSession, prompt_id: uuid.UUID) -> Prompt:
    p = await session.get(Prompt, prompt_id)
    if p is None or p.archived_at is not None:
        raise NotFoundError("prompt", str(prompt_id))
    return p


async def list_prompts(session: AsyncSession, *, project_id: uuid.UUID) -> list[Prompt]:
    return await list_latest_per_slug(session, Prompt, project_id=project_id)


async def get_prompt_latest(session: AsyncSession, *, project_id: uuid.UUID, slug: str) -> Prompt:
    return await get_latest(session, Prompt, project_id=project_id, slug=slug)


async def get_prompt_version(
    session: AsyncSession, *, project_id: uuid.UUID, slug: str, version: int
) -> Prompt:
    return await get_version(session, Prompt, project_id=project_id, slug=slug, version=version)


async def archive_prompt(session: AsyncSession, prompt_id: uuid.UUID) -> Prompt:
    p = await get_prompt(session, prompt_id)
    p.archived_at = datetime.now(UTC)
    await session.flush()
    return p
