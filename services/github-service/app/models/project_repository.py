"""ProjectRepository ORM model linking one CodeBridge project to one active GitHub repository."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utc_now

if TYPE_CHECKING:
    from app.models.github_connection import GitHubConnection


class ProjectRepositoryStatus(str, enum.Enum):
    """Connection status between a CodeBridge project and a GitHub repository."""

    ACTIVE = "ACTIVE"
    DISCONNECTED = "DISCONNECTED"


class ProjectRepository(Base):
    """GitHub repository association & authoritative metadata scoped to a project."""

    __tablename__ = "project_repositories"
    __table_args__ = (
        Index(
            "uq_project_repositories_one_active_per_project",
            "project_id",
            unique=True,
            postgresql_where=text("connection_status = 'ACTIVE'"),
            sqlite_where=text("connection_status = 'ACTIVE'"),
        ),
        Index(
            "ix_project_repositories_project_status",
            "project_id",
            "connection_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        index=True,
        nullable=False,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        index=True,
        nullable=False,
    )
    github_connection_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("github_connections.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    github_repository_id: Mapped[int] = mapped_column(
        BigInteger,
        index=True,
        nullable=False,
    )
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    repository_name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_login: Mapped[str] = mapped_column(String(255), nullable=False)
    is_private: Mapped[bool] = mapped_column(Boolean, nullable=False)
    default_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    html_url: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_fork: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    connection_status: Mapped[ProjectRepositoryStatus] = mapped_column(
        Enum(
            ProjectRepositoryStatus,
            name="project_repository_status",
            native_enum=False,
        ),
        nullable=False,
        default=ProjectRepositoryStatus.ACTIVE,
        index=True,
    )
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
    disconnected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    github_connection: Mapped[GitHubConnection] = relationship(
        back_populates="project_repositories"
    )

