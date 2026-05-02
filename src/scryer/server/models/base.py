"""SQLAlchemy declarative base + naming convention + reusable mixins.

Naming convention is critical for Alembic autogenerate to produce stable,
predictable constraint names across migrations. See plan §11.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, MetaData
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def utc_now() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    """Adds created_at / updated_at to a model.

    `updated_at` is application-managed (set on UPDATE via ORM event) — DB-side
    triggers add cross-DB drift and complicate Alembic autogen.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class SoftDeleteMixin:
    """archived_at nullable; all read queries should filter WHERE archived_at IS NULL."""

    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True
    )


class VersionedMixin:
    """For content-hashed, lineage-tracked resources (Dataset, Scorer, Agent,
    Tool, Prompt, Task per plan §11). Every version is a separate immutable
    row; (project_id, slug, version) is unique; parent_id chains lineage.

    Tables using this mixin must add (project_id, slug, version) UNIQUE in
    their own __table_args__.
    """

    import uuid as _uuid_mod

    from sqlalchemy import ForeignKey as _FK
    from sqlalchemy import Integer as _Int
    from sqlalchemy import String as _Str
    from sqlalchemy import Uuid as _Uuid

    project_id: Mapped[_uuid_mod.UUID] = mapped_column(
        _Uuid, _FK("projects.id", ondelete="CASCADE"), nullable=False
    )
    slug: Mapped[str] = mapped_column(_Str(64), nullable=False)
    version: Mapped[int] = mapped_column(_Int, nullable=False)
    content_hash: Mapped[str] = mapped_column(_Str(64), nullable=False)  # sha256 hex
    parent_id: Mapped[_uuid_mod.UUID | None] = mapped_column(
        _Uuid,
        nullable=True,  # FK target varies per table; use string ref in models
    )
