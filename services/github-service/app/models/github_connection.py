"""GitHubConnection ORM model storing owner GitHub identity and encrypted OAuth credentials."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, Enum, Index, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utc_now

if TYPE_CHECKING:
    from app.models.project_repository import ProjectRepository


class GitHubConnectionStatus(str, enum.Enum):
    """Lifecycle states for a CodeBridge owner's GitHub OAuth connection."""

    ACTIVE = "ACTIVE"
    DISCONNECTED = "DISCONNECTED"
    REVOKED = "REVOKED"


class GitHubConnection(Base):
    """Encrypted GitHub OAuth connection owned by a single CodeBridge OWNER."""

    __tablename__ = "github_connections"
    __table_args__ = (
        Index(
            "uq_github_connections_active_owner",
            "owner_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
            sqlite_where=text("status = 'ACTIVE'"),
        ),
        Index(
            "ix_github_connections_owner_gh_user",
            "owner_id",
            "github_user_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        index=True,
        nullable=False,
    )
    github_user_id: Mapped[int] = mapped_column(
        BigInteger,
        index=True,
        nullable=False,
    )
    github_login: Mapped[str] = mapped_column(String(255), nullable=False)
    encrypted_access_token: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[GitHubConnectionStatus] = mapped_column(
        Enum(GitHubConnectionStatus, name="github_connection_status", native_enum=False),
        nullable=False,
        default=GitHubConnectionStatus.ACTIVE,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
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
    last_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    disconnected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    project_repositories: Mapped[list[ProjectRepository]] = relationship(
        back_populates="github_connection",
        cascade="all, delete-orphan",
    )

