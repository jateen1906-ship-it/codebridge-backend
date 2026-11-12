"""IndexedFile ORM model tracking discovered, indexed, and skipped repository files."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utc_now


class IndexedFileStatus(str, enum.Enum):
    """Per-file outcome during repository ingestion."""

    INDEXED = "INDEXED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


class IndexedFile(Base):
    """Metadata record for an individual file evaluated during repository indexing."""

    __tablename__ = "indexed_files"
    __table_args__ = (
        UniqueConstraint("repository_index_id", "file_path", name="uq_indexed_file_index_path"),
        Index("ix_indexed_files_index_status", "repository_index_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repository_index_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("repository_indexes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_extension: Mapped[str | None] = mapped_column(String(32), nullable=True)
    language: Mapped[str | None] = mapped_column(String(64), nullable=True)
    file_sha: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[IndexedFileStatus] = mapped_column(
        SAEnum(IndexedFileStatus, native_enum=False, length=20),
        nullable=False,
    )
    skip_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    indexed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

