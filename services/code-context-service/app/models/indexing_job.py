"""IndexingJob ORM model tracking individual index and reindex executions."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utc_now


class IndexingJobType(str, enum.Enum):
    """Operation type for an indexing execution."""

    INITIAL = "INITIAL"
    REINDEX = "REINDEX"


class IndexingJobStatus(str, enum.Enum):
    """Execution status for an indexing job."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class IndexingJob(Base):
    """Audit and concurrency-guard record for indexing / reindexing runs."""

    __tablename__ = "indexing_jobs"
    __table_args__ = (
        Index(
            "uq_indexing_job_one_inflight_per_project",
            "project_id",
            unique=True,
            postgresql_where=text("status IN ('PENDING', 'RUNNING')"),
            sqlite_where=text("status IN ('PENDING', 'RUNNING')"),
        ),
        Index("ix_indexing_jobs_project_created", "project_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    repository_index_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("repository_indexes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    triggered_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    job_type: Mapped[IndexingJobType] = mapped_column(
        SAEnum(IndexingJobType, native_enum=False, length=20),
        nullable=False,
    )
    status: Mapped[IndexingJobStatus] = mapped_column(
        SAEnum(IndexingJobStatus, native_enum=False, length=20),
        nullable=False,
        default=IndexingJobStatus.PENDING,
    )
    branch: Mapped[str] = mapped_column(String(255), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    files_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    files_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunks_generated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
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

