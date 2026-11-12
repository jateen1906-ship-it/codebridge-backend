"""RepositoryIndex ORM model and IndexStatus enumeration."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum as SAEnum,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utc_now


class IndexStatus(str, enum.Enum):
    """Lifecycle status of a versioned project repository index."""

    PENDING = "PENDING"
    INDEXING = "INDEXING"
    READY = "READY"
    FAILED = "FAILED"
    STALE = "STALE"


class RepositoryIndex(Base):
    """Metadata tracking a versioned repository index build for one CodeBridge project."""

    __tablename__ = "repository_indexes"
    __table_args__ = (
        UniqueConstraint("project_id", "index_version", name="uq_repo_index_project_version"),
        Index(
            "uq_repo_index_one_active_per_project",
            "project_id",
            unique=True,
            postgresql_where=text("is_active IS TRUE"),
            sqlite_where=text("is_active = 1"),
        ),
        Index("ix_repo_indexes_project_status", "project_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    github_repository_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    repository_full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    branch: Mapped[str] = mapped_column(String(255), nullable=False)
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    index_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    collection_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    embedding_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[IndexStatus] = mapped_column(
        SAEnum(IndexStatus, native_enum=False, length=20),
        nullable=False,
        default=IndexStatus.PENDING,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    files_discovered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    files_indexed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    files_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunks_generated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_bytes_indexed: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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

