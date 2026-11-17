"""Declarative base, naming conventions, and reusable database mixins."""

from datetime import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, MetaData, String, Uuid, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models in Nexus Backend."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class AuditTimestampMixin:
    """Audit timestamps and creator UUID shared across platform entities."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
    )


class TenantChildMixin(AuditTimestampMixin):
    """Standard base mixin for tenant-owned child tables (non-root entities).

    Every tenant-owned child table carries a NOT NULL indexed ``workspace_id``
    foreign key referencing ``workspaces.id``. Composite indexes on child
    tables must lead with ``workspace_id``.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )


class ExternalProvenanceMixin:
    """Reusable provenance mixin for records synchronized from external systems.

    Intended only for entities ingested from external ERP/CRM/supplier
    integrations (not natively created records like ``workspaces``). Future
    external tables should pair these columns with a composite unique
    constraint ``UniqueConstraint("workspace_id", "source_system", "source_ref")``.
    """

    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    source_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
