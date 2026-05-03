"""Task service: bind (Dataset, Scorer, Agent?, Prompt?) at specific versions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.models.eval import Agent, Dataset, Prompt, Scorer, Task
from scryer.server.services._versioned import (
    content_hash,
    get_latest,
    list_latest_per_slug,
    next_version,
)
from scryer.server.services.errors import NotFoundError, ValidationError


async def push_task(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    slug: str,
    name: str,
    dataset_id: uuid.UUID,
    dataset_version: int,
    scorer_id: uuid.UUID,
    scorer_version: int,
    agent_id: uuid.UUID | None = None,
    agent_version: int | None = None,
    prompt_id: uuid.UUID | None = None,
    prompt_version: int | None = None,
    params_json: dict[str, Any] | None = None,
    estimated_cost_usd: Decimal | None = None,
    description: str | None = None,
) -> Task:
    """Validates referenced (id, version) pairs exist, then pins them."""
    if (agent_id is None) != (agent_version is None):
        raise ValidationError("agent_id and agent_version must both be set or both be None")
    if (prompt_id is None) != (prompt_version is None):
        raise ValidationError("prompt_id and prompt_version must both be set or both be None")

    await _check_version(session, Dataset, dataset_id, dataset_version, "dataset")
    await _check_version(session, Scorer, scorer_id, scorer_version, "scorer")
    if agent_id is not None:
        assert agent_version is not None
        await _check_version(session, Agent, agent_id, agent_version, "agent")
    if prompt_id is not None:
        assert prompt_version is not None
        await _check_version(session, Prompt, prompt_id, prompt_version, "prompt")

    version, parent_id = await next_version(session, Task, project_id=project_id, slug=slug)
    # FROZEN CONTRACT (Task.content_hash): references-not-content.
    # Hashes the bound child (id, version) pairs + params_json. Git-style:
    # a Task is a "commit" pinning specific child versions. Identity IS
    # those references, NOT the children's contents. Changing
    # dataset_version from 2 to 3 is a different Task even if both versions
    # of the dataset have the same content_hash.
    # Lock-in test: tests/test_content_hash_stability.py.
    h = content_hash(
        {
            "dataset_id": str(dataset_id),
            "dataset_version": dataset_version,
            "scorer_id": str(scorer_id),
            "scorer_version": scorer_version,
            "agent_id": str(agent_id) if agent_id else None,
            "agent_version": agent_version,
            "prompt_id": str(prompt_id) if prompt_id else None,
            "prompt_version": prompt_version,
            "params_json": params_json,
        }
    )
    t = Task(
        project_id=project_id,
        slug=slug,
        version=version,
        content_hash=h,
        parent_id=parent_id,
        name=name,
        description=description,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        scorer_id=scorer_id,
        scorer_version=scorer_version,
        agent_id=agent_id,
        agent_version=agent_version,
        prompt_id=prompt_id,
        prompt_version=prompt_version,
        params_json=params_json,
        estimated_cost_usd=estimated_cost_usd,
    )
    session.add(t)
    await session.flush()
    return t


async def _check_version(
    session: AsyncSession,
    model: type[Any],
    rid: uuid.UUID,
    expected_version: int,
    label: str,
) -> None:
    row = await session.get(model, rid)
    if row is None or row.version != expected_version:
        raise NotFoundError(label, f"{rid}@v{expected_version}")


async def get_task(session: AsyncSession, task_id: uuid.UUID) -> Task:
    t = await session.get(Task, task_id)
    if t is None or t.archived_at is not None:
        raise NotFoundError("task", str(task_id))
    return t


async def get_task_latest(session: AsyncSession, *, project_id: uuid.UUID, slug: str) -> Task:
    return await get_latest(session, Task, project_id=project_id, slug=slug)


async def list_tasks(session: AsyncSession, *, project_id: uuid.UUID) -> list[Task]:
    return await list_latest_per_slug(session, Task, project_id=project_id)


async def archive_task(session: AsyncSession, task_id: uuid.UUID) -> Task:
    t = await get_task(session, task_id)
    t.archived_at = datetime.now(UTC)
    await session.flush()
    return t
