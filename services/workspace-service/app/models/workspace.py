"""Workspace ORM model and controlled lifecycle state definitions."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utc_now


class WorkspaceStatus(str, enum.Enum):
    """Controlled lifecycle states for isolated developer workspaces."""

    PROVISIONING = "PROVISIONING"
    READY = "READY"
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    DESTROYED = "DESTROYED"


TERMINAL_WORKSPACE_STATUSES: frozenset[WorkspaceStatus] = frozenset(
    {
        WorkspaceStatus.FAILED,
        WorkspaceStatus.EXPIRED,
        WorkspaceStatus.DESTROYED,
    }
)


class Workspace(Base):
    """Isolated developer execution workspace owned exclusively by Workspace Service."""

    __tablename__ = "workspaces"
    __table_args__ = (
        CheckConstraint("memory_limit_mb >= 64", name="ck_workspaces_memory_min"),
        CheckConstraint("pids_limit >= 16", name="ck_workspaces_pids_min"),
        CheckConstraint("storage_bytes >= 0", name="ck_workspaces_storage_non_negative"),
        CheckConstraint("file_count >= 0", name="ck_workspaces_file_count_non_negative"),
        Index(
            "uq_workspaces_one_active_per_project",
            "project_id",
            unique=True,
            sqlite_where=text("status NOT IN ('FAILED', 'EXPIRED', 'DESTROYED')"),
            postgresql_where=text("status NOT IN ('FAILED', 'EXPIRED', 'DESTROYED')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True, nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True, nullable=False)
    assigned_freelancer_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        index=True,
        nullable=True,
    )
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True, nullable=False)

    repository_full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    repository_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    snapshot_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)

    status: Mapped[WorkspaceStatus] = mapped_column(
        Enum(WorkspaceStatus, name="workspace_status", native_enum=False),
        nullable=False,
        default=WorkspaceStatus.PROVISIONING,
        index=True,
    )
    container_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    container_name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    workspace_path: Mapped[str] = mapped_column(String(500), nullable=False)
    runtime_image: Mapped[str] = mapped_column(String(128), nullable=False)

    cpu_limit: Mapped[str] = mapped_column(String(16), nullable=False, default="1.0")
    memory_limit_mb: Mapped[int] = mapped_column(Integer, nullable=False, default=512)
    pids_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=128)
    network_disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    storage_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    destroyed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    events = relationship(
        "WorkspaceEvent",
        back_populates="workspace",
        cascade="all, delete-orphan",
        order_by="WorkspaceEvent.created_at.desc()",
    )

