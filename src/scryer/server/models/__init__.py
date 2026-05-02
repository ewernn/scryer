"""SQLAlchemy ORM models grouped by schema cluster.

Imports below register all models with `Base.metadata` so Alembic autogenerate
sees them and so `Base.metadata.create_all()` works in tests.
"""

from scryer.server.models.auth import (  # noqa: F401
    ApiKey,
    ApiKeyUsage,
    Budget,
    Credential,
    Invitation,
    Project,
    ProjectMember,
    ProjectSlug,
    ServiceAccount,
    ShareGrant,
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceSlug,
)
from scryer.server.models.base import Base  # noqa: F401

__all__ = [
    "ApiKey",
    "ApiKeyUsage",
    "Base",
    "Budget",
    "Credential",
    "Invitation",
    "Project",
    "ProjectMember",
    "ProjectSlug",
    "ServiceAccount",
    "ShareGrant",
    "User",
    "Workspace",
    "WorkspaceMember",
    "WorkspaceSlug",
]
