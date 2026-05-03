"""Cluster 3: Activity + Audit + Governance + Automation.

Tables:
- audit_events (BIGSERIAL append-only; per plan §8)
- suites + suite_tasks + suite_runs + suite_run_runs
- triggers
- webhooks + webhook_deliveries
- tags + resource_tags
- usage_records (BIGSERIAL per LLM call)

Polymorphic actor pattern repeats from cluster 1 (api_keys.principal).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
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
)
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from scryer.server.models.base import Base, TimestampMixin
from scryer.server.models.enums import (
    ActorKind,
    MissedFirePolicy,
    TriggerKind,
    TriggerTarget,
    WebhookDeliveryStatus,
)

# ── audit_events ────────────────────────────────────────────────────────────


class AuditEvent(Base):
    """Append-only audit log. Per plan §8 — three layers; this is layer 2.

    Actor is polymorphic (User | ServiceAccount | cron | system) via
    actor_kind discriminator; concrete user/sa FKs are nullable.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        # actor consistency: kind matches which (or no) FK is set
        CheckConstraint(
            "(actor_kind = 'user' AND actor_user_id IS NOT NULL "
            "AND actor_service_account_id IS NULL) OR "
            "(actor_kind = 'service_account' AND actor_service_account_id IS NOT NULL "
            "AND actor_user_id IS NULL) OR "
            "(actor_kind IN ('cron','system') AND actor_user_id IS NULL "
            "AND actor_service_account_id IS NULL)",
            name="actor_kind_matches_fks",
        ),
        Index("ix_audit_events_workspace_id_id", "workspace_id", "id"),
        Index("ix_audit_events_resource_type_resource_id_id", "resource_type", "resource_id", "id"),
        Index("ix_audit_events_actor_user_id_id", "actor_user_id", "id"),
        Index("ix_audit_events_request_id", "request_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )

    actor_kind: Mapped[ActorKind] = mapped_column(
        Enum(ActorKind, name="actor_kind", native_enum=False, length=32), nullable=False
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_service_account_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("service_accounts.id", ondelete="SET NULL"), nullable=True
    )
    on_behalf_of_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    before_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parent_event_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("audit_events.id", ondelete="SET NULL"), nullable=True
    )

    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


# ── suites + suite_runs ─────────────────────────────────────────────────────


class Suite(Base, TimestampMixin):
    __tablename__ = "suites"
    __table_args__ = (UniqueConstraint("project_id", "slug", name="uq_suites_project_slug"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class SuiteTask(Base):
    __tablename__ = "suite_tasks"
    __table_args__ = (UniqueConstraint("suite_id", "task_id", name="uq_suite_tasks_suite_task"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    suite_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("suites.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    weight: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False, default=1)


class SuiteRun(Base, TimestampMixin):
    __tablename__ = "suite_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    suite_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("suites.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    triggered_by_kind: Mapped[ActorKind] = mapped_column(
        Enum(ActorKind, name="actor_kind", native_enum=False, length=32), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SuiteRunRun(Base):
    __tablename__ = "suite_run_runs"
    __table_args__ = (
        UniqueConstraint("suite_run_id", "run_id", name="uq_suite_run_runs_suite_run"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    suite_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("suite_runs.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )


# ── triggers ────────────────────────────────────────────────────────────────


class Trigger(Base, TimestampMixin):
    __tablename__ = "triggers"
    __table_args__ = (
        Index(
            "ix_triggers_active_next_fire_at",
            "is_active",
            "next_fire_at",
            postgresql_where="is_active",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[TriggerKind] = mapped_column(
        Enum(TriggerKind, name="trigger_kind", native_enum=False, length=16), nullable=False
    )
    target_kind: Mapped[TriggerTarget] = mapped_column(
        Enum(TriggerTarget, name="trigger_target", native_enum=False, length=16), nullable=False
    )
    target_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)

    cron_expression: Mapped[str | None] = mapped_column(String(64), nullable=True)
    next_fire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    missed_fire_policy: Mapped[MissedFirePolicy] = mapped_column(
        Enum(MissedFirePolicy, name="missed_fire_policy", native_enum=False, length=32),
        nullable=False,
        default=MissedFirePolicy.skip_to_latest,
    )
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)


# ── webhooks ────────────────────────────────────────────────────────────────


class Webhook(Base, TimestampMixin):
    __tablename__ = "webhooks"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    secret: Mapped[str] = mapped_column(String(64), nullable=False)  # HMAC key
    event_types: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        Index(
            "ix_webhook_deliveries_status_next_attempt_at",
            "status",
            "next_attempt_at",
            postgresql_where="status IN ('pending','failed')",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    webhook_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[WebhookDeliveryStatus] = mapped_column(
        Enum(WebhookDeliveryStatus, name="webhook_delivery_status", native_enum=False, length=16),
        nullable=False,
        default=WebhookDeliveryStatus.pending,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ── tags + resource_tags ────────────────────────────────────────────────────


class Tag(Base, TimestampMixin):
    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_tags_workspace_name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)


class ResourceTag(Base):
    __tablename__ = "resource_tags"
    __table_args__ = (
        UniqueConstraint("tag_id", "resource_type", "resource_id", name="uq_resource_tags_unique"),
        Index("ix_resource_tags_resource", "resource_type", "resource_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tag_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tags.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)


# ── usage_records ───────────────────────────────────────────────────────────


class UsageRecord(Base):
    __tablename__ = "usage_records"
    __table_args__ = (
        Index("ix_usage_records_workspace_id_timestamp", "workspace_id", "timestamp"),
        Index("ix_usage_records_run_id", "run_id", postgresql_where="run_id IS NOT NULL"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="SET NULL"), nullable=True
    )
    credential_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("credentials.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
