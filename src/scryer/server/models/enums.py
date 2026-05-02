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
