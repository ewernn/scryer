"""Trajectory writer: persists a per-record execution capture either inline
(N TrajectoryStep rows in PG) or, when total payload exceeds the inline
threshold, as a single JSON blob in R2 with `Trajectory.storage_uri`
pointing at it.

Plan §15 #17 ordering: when spilling, R2 write happens FIRST and the PG row
only commits if R2 succeeded. If PG flush later fails, the R2 blob is
orphaned and reaped by a separate GC job (not built yet)."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.models.enums import TrajectoryStorage
from scryer.server.models.eval import Trajectory, TrajectoryStep
from scryer.server.services.blobs import make_blob_key, put_blob

# Switch to R2 once the serialized JSON payload of all steps would exceed
# this many bytes. Below the threshold, individual TrajectoryStep rows in
# PG are cheaper to query (record_id-keyed lookups, partial JSONB filters).
INLINE_THRESHOLD_BYTES = 4096


async def write_trajectory(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    record_id: int,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    steps: list[dict[str, Any]],
) -> Trajectory:
    """Persist a Trajectory + its steps. Each step dict is shaped:
    {"seq": int, "kind": str, "tool_id"?: UUID, "payload"?: dict,
     "started_at"?: datetime, "duration_ms"?: int}
    """
    trajectory_id = uuid.uuid4()
    serialized = json.dumps(steps, default=_json_default, sort_keys=True).encode("utf-8")

    if len(serialized) <= INLINE_THRESHOLD_BYTES:
        trajectory = Trajectory(
            id=trajectory_id,
            run_id=run_id,
            record_id=record_id,
            storage=TrajectoryStorage.inline,
            n_steps=len(steps),
        )
        session.add(trajectory)
        await session.flush()
        for step in steps:
            session.add(
                TrajectoryStep(
                    trajectory_id=trajectory.id,
                    seq=step["seq"],
                    kind=step["kind"],
                    tool_id=step.get("tool_id"),
                    payload_json=step.get("payload"),
                    started_at=step.get("started_at"),
                    duration_ms=step.get("duration_ms"),
                )
            )
        await session.flush()
        return trajectory

    # Spill path. R2 write FIRST, then PG row. If R2 fails, exception
    # propagates and no PG row exists.
    key = make_blob_key(
        workspace_id=workspace_id,
        project_id=project_id,
        kind="trajectories",
        relpath=f"{run_id}/{record_id}.json",
    )
    uri, sha = await put_blob(key, serialized, content_type="application/json")

    trajectory = Trajectory(
        id=trajectory_id,
        run_id=run_id,
        record_id=record_id,
        storage=TrajectoryStorage.r2,
        storage_uri=uri,
        storage_sha256=sha,
        n_steps=len(steps),
    )
    session.add(trajectory)
    await session.flush()
    return trajectory


def _json_default(obj: Any) -> Any:
    """Make UUID, datetime serializable for the spill payload."""
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    raise TypeError(f"not JSON-serializable: {type(obj).__name__}")
