"""Cluster 2: Evaluation Core.

12 tables per plan §11:
- datasets, dataset_records
- scorers, agents, tools, prompts (versioned via VersionedMixin)
- agent_tools junction
- tasks (versioned binding)
- runs, results, traces, trace_steps

All FKs are concrete (no polymorphic FK pattern in this cluster).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from scryer.server.models.base import (
    Base,
    SoftDeleteMixin,
    TimestampMixin,
    VersionedMixin,
)
from scryer.server.models.enums import (
    PromptTemplateFormat,
    RunStatus,
    TraceStorage,
)

# ── datasets + dataset_records ──────────────────────────────────────────────


class Dataset(Base, VersionedMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "datasets"
    __table_args__ = (
        UniqueConstraint("project_id", "slug", "version", name="uq_datasets_project_slug_version"),
        Index("ix_datasets_content_hash", "content_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    schema_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Provenance: when an agent curates a Dataset from failed Results
    source_results: Mapped[list[uuid.UUID] | None] = mapped_column(ARRAY(Uuid), nullable=True)


class DatasetRecord(Base):
    """Immutable per-Dataset records. Composite PK (dataset_id, record_id)."""

    __tablename__ = "dataset_records"
    __table_args__ = (Index("ix_dataset_records_inputs_gin", "inputs", postgresql_using="gin"),)

    dataset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("datasets.id", ondelete="CASCADE"), primary_key=True
    )
    record_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    expected: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").UTC),
        nullable=False,
    )


# ── scorers, agents, tools, prompts ─────────────────────────────────────────


class Scorer(Base, VersionedMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "scorers"
    __table_args__ = (
        UniqueConstraint("project_id", "slug", "version", name="uq_scorers_project_slug_version"),
        Index("ix_scorers_content_hash", "content_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    server_executable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Agent(Base, VersionedMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint("project_id", "slug", "version", name="uq_agents_project_slug_version"),
        Index("ix_agents_config_json_gin", "config_json", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    config_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class Tool(Base, VersionedMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "tools"
    __table_args__ = (
        UniqueConstraint("project_id", "slug", "version", name="uq_tools_project_slug_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    schema_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    sandbox_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class AgentTool(Base):
    __tablename__ = "agent_tools"
    __table_args__ = (UniqueConstraint("agent_id", "tool_id", name="uq_agent_tools_agent_tool"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    tool_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tools.id", ondelete="CASCADE"), nullable=False
    )


class Prompt(Base, VersionedMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "prompts"
    __table_args__ = (
        UniqueConstraint("project_id", "slug", "version", name="uq_prompts_project_slug_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    template: Mapped[str] = mapped_column(Text, nullable=False)
    template_format: Mapped[PromptTemplateFormat] = mapped_column(
        Enum(PromptTemplateFormat, name="prompt_template_format", native_enum=False, length=16),
        nullable=False,
        default=PromptTemplateFormat.fstring,
    )


# ── tasks ────────────────────────────────────────────────────────────────────


class Task(Base, VersionedMixin, TimestampMixin, SoftDeleteMixin):
    """A Task binds (Dataset, Scorer, Agent?, Prompt?) at specific versions.
    content_hash includes all four version pins so re-binding mints a new Task version."""

    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("project_id", "slug", "version", name="uq_tasks_project_slug_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("datasets.id", ondelete="RESTRICT"), nullable=False
    )
    dataset_version: Mapped[int] = mapped_column(Integer, nullable=False)
    scorer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("scorers.id", ondelete="RESTRICT"), nullable=False
    )
    scorer_version: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agents.id", ondelete="RESTRICT"), nullable=True
    )
    agent_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompt_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("prompts.id", ondelete="RESTRICT"), nullable=True
    )
    prompt_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    params_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)


# ── runs ─────────────────────────────────────────────────────────────────────


class Run(Base, TimestampMixin):
    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','done','failed','cancelled','superseded')",
            name="status_valid",
        ),
        CheckConstraint(
            "(status = 'superseded') = (superseded_by_run_id IS NOT NULL)",
            name="superseded_coherent",
        ),
        Index(
            "ix_runs_status_queued_at",
            "status",
            "created_at",
            postgresql_where="status IN ('queued','running')",
        ),
        Index(
            "ix_runs_last_heartbeat_at", "last_heartbeat_at", postgresql_where="status = 'running'"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=False
    )
    task_version: Mapped[int] = mapped_column(Integer, nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, name="run_status", native_enum=False, length=16),
        nullable=False,
        default=RunStatus.queued,
    )
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resume_cursor: Mapped[int | None] = mapped_column(Integer, nullable=True)

    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invalidation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    superseded_by_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="SET NULL"), nullable=True
    )

    n_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


# ── results ──────────────────────────────────────────────────────────────────


class Result(Base):
    __tablename__ = "results"
    __table_args__ = (
        Index(
            "ix_results_run_id_record_id",
            "run_id",
            "record_id",
            postgresql_where=text("invalidated_at IS NULL"),
        ),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True
    )
    record_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    score_value: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    score_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").UTC),
        nullable=False,
    )


# ── traces + trace_steps ─────────────────────────────────────────────────────


class Trace(Base, TimestampMixin):
    """Per-record execution capture. Small traces inline (trace_steps);
    large traces spill to R2 (storage_uri + storage_sha256)."""

    __tablename__ = "traces"
    __table_args__ = (
        UniqueConstraint("run_id", "record_id", name="uq_traces_run_record"),
        CheckConstraint(
            "(storage = 'inline') OR (storage_uri IS NOT NULL AND storage_sha256 IS NOT NULL)",
            name="r2_has_uri_and_checksum",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    record_id: Mapped[int] = mapped_column(Integer, nullable=False)
    storage: Mapped[TraceStorage] = mapped_column(
        Enum(TraceStorage, name="trace_storage", native_enum=False, length=16),
        nullable=False,
        default=TraceStorage.inline,
    )
    storage_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    n_steps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class TraceStep(Base):
    """Normalized step rows for inline traces. tool_id FK enables cross-Trace
    'where was Tool X used' queries."""

    __tablename__ = "trace_steps"
    __table_args__ = (
        Index("ix_trace_steps_trace_id_seq", "trace_id", "seq", unique=True),
        Index("ix_trace_steps_tool_id", "tool_id", postgresql_where="tool_id IS NOT NULL"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("traces.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    tool_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("tools.id", ondelete="SET NULL"), nullable=True
    )
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
