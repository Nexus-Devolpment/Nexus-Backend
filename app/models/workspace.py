"""Workspace ORM model representing the root tenant entity (AM-01)."""

import uuid

from sqlalchemy import CheckConstraint, Index, String, UniqueConstraint, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import AuditTimestampMixin, Base


class Workspace(AuditTimestampMixin, Base):
    """Root tenant entity representing an isolated company workspace.

    Option A design exception: ``workspaces`` is keyed by ``id`` rather than a
    self-referencing ``workspace_id`` column. All tenant-owned child tables
    must inherit ``TenantChildMixin`` and reference ``workspaces.id``.
    """

    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(63), nullable=False)

    __table_args__ = (
        UniqueConstraint("slug", name="uq_workspaces_slug"),
        CheckConstraint(
            "btrim(name) <> '' AND name = btrim(name)",
            name="name_not_blank",
        ),
        CheckConstraint(
            "slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND char_length(slug) >= 2",
            name="slug_format",
        ),
        CheckConstraint(
            "updated_at >= created_at",
            name="updated_at_gte_created_at",
        ),
        Index("ix_workspaces_created_by", "created_by"),
    )
