"""Cluster 4: Collaboration — Comments + Collections.

Per plan §10. Comments are flat (single-level reply via reply_to_comment_id);
structured to {tldr, confidence}; references are opaque pointers (not joined
FKs — resource_type discriminator + uuid/string fragment).

Collections bundle pointers to multiple resources with optional grouping.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from scryer.server.models.base import Base, TimestampMixin
from scryer.server.models.enums import (
    ActorKind,
    CollectionPurpose,
    CommentKind,
)


class Comment(Base, TimestampMixin):
    """Flat timestamped comment. References JSONB stores opaque pointers."""

    __tablename__ = "comments"
    __table_args__ = (
        Index("ix_comments_project_id_id", "project_id", "id"),
        Index("ix_comments_resource_type_resource_id", "resource_type", "resource_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    # Optional resource anchor (Comments can be project-level or resource-scoped)
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    # Author is polymorphic across User / ServiceAccount / system
    author_kind: Mapped[ActorKind] = mapped_column(
        Enum(ActorKind, name="actor_kind", native_enum=False, length=32), nullable=False
    )
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    author_service_account_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("service_accounts.id", ondelete="SET NULL"), nullable=True
    )

    kind: Mapped[CommentKind] = mapped_column(
        Enum(CommentKind, name="comment_kind", native_enum=False, length=16), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # Slim per Option A: only tldr + confidence. Plan-by-stealth blocked.
    structured: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    references: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    mentions: Mapped[list[uuid.UUID] | None] = mapped_column(JSONB, nullable=True)

    reply_to_comment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("comments.id", ondelete="SET NULL"), nullable=True
    )


class CommentVersion(Base):
    """Edit history for a Comment."""

    __tablename__ = "comment_versions"
    __table_args__ = (
        UniqueConstraint("comment_id", "version", name="uq_comment_versions_comment_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    comment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("comments.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    structured: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    edited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Collection(Base, TimestampMixin):
    __tablename__ = "collections"
    __table_args__ = (UniqueConstraint("project_id", "slug", name="uq_collections_project_slug"),)

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
    purpose: Mapped[CollectionPurpose] = mapped_column(
        Enum(CollectionPurpose, name="collection_purpose", native_enum=False, length=32),
        nullable=False,
        default=CollectionPurpose.investigation,
    )


class CollectionMember(Base):
    """Pointer from a Collection to any resource. group + note add structure."""

    __tablename__ = "collection_members"
    __table_args__ = (
        UniqueConstraint(
            "collection_id",
            "member_type",
            "member_id",
            name="uq_collection_members_unique",
        ),
        Index("ix_collection_members_collection_position", "collection_id", "position"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    collection_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("collections.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    member_type: Mapped[str] = mapped_column(String(64), nullable=False)
    member_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    group: Mapped[str | None] = mapped_column(String(128), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
