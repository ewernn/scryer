"""Enum types used across cluster 1 (and beyond).

Stored as native PG enums (`sa.Enum(..., name="...")`). Native enums require
explicit DROP TYPE in tests if using `drop_all`; we use savepoint rollback
fixtures so this is not an issue.
"""

from __future__ import annotations

from enum import StrEnum


class PrincipalKind(StrEnum):
    """For polymorphic FK discriminators (api_keys, budgets, audit actor)."""

    user = "user"
    service_account = "service_account"


class ProjectVisibility(StrEnum):
    """Plan §5: hybrid model. `private` = explicit member list only;
    `workspace` = all workspace members per their workspace role."""

    private = "private"
    workspace = "workspace"


class WorkspaceRole(StrEnum):
    """Plan §5: viewer / member / owner at Workspace and Project level."""

    viewer = "viewer"
    member = "member"
    owner = "owner"


class ProjectRole(StrEnum):
    viewer = "viewer"
    member = "member"
    owner = "owner"


class ApiScope(StrEnum):
    """Plan §6: 4-scope vocabulary (reduced from 8 GitHub-PAT-style)."""

    read = "read"
    write = "write"
    admin = "admin"
    impersonate = "impersonate"


class CredentialProvider(StrEnum):
    openai = "openai"
    anthropic = "anthropic"
    openrouter = "openrouter"
    custom = "custom"


class SharePermission(StrEnum):
    read = "read"
    write = "write"


class BudgetPeriod(StrEnum):
    daily = "daily"
    weekly = "weekly"
    monthly = "monthly"


class InvitationStatus(StrEnum):
    pending = "pending"
    used = "used"
    expired = "expired"
    revoked = "revoked"


class PromptTemplateFormat(StrEnum):
    """Format for Prompt.template — controls how variables are interpolated."""

    plain = "plain"  # no interpolation; literal string
    fstring = "fstring"  # Python f-string-style {var}
    jinja = "jinja"  # Jinja2 templating


class RunStatus(StrEnum):
    """Run lifecycle. Transitions enforced in service layer + DB CHECK."""

    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"
    superseded = "superseded"


class TraceStorage(StrEnum):
    """Where trace steps live. Set at flush time based on n_steps."""

    inline = "inline"  # trace_steps table
    r2 = "r2"  # storage_uri populated


class ActorKind(StrEnum):
    """For audit_events.actor_kind discriminator (plus 'cron' / 'system')."""

    user = "user"
    service_account = "service_account"
    cron = "cron"
    system = "system"


class TriggerKind(StrEnum):
    schedule = "schedule"
    webhook = "webhook"


class TriggerTarget(StrEnum):
    task = "task"
    suite = "suite"


class MissedFirePolicy(StrEnum):
    skip_to_latest = "skip_to_latest"
    fire_all = "fire_all"


class WebhookDeliveryStatus(StrEnum):
    pending = "pending"
    delivered = "delivered"
    failed = "failed"
    dead_letter = "dead_letter"
