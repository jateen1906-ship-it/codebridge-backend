"""Submission and SubmissionFile ORM models owned by Review Service."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SubmissionStatus(str, Enum):
    """Canonical lifecycle states for freelancer project submissions."""

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"


class FileChangeType(str, Enum):
    """Modified file classification inside a submission summary."""

    ADDED = "ADDED"
    MODIFIED = "MODIFIED"
    DELETED = "DELETED"


class Submission(Base):
    """Freelancer deliverable submission for an assigned project."""

    __tablename__ = "submissions"
    __table_args__ = (
        UniqueConstraint("project_id", "version_number", name="uq_submissions_project_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    freelancer_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    changes_summary: Mapped[str] = mapped_column(Text, nullable=False)
    test_results_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    git_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=SubmissionStatus.DRAFT.value,
        index=True,
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    files: Mapped[list["SubmissionFile"]] = relationship(
        back_populates="submission",
        cascade="all, delete-orphan",
        order_by="SubmissionFile.file_path.asc()",
    )
    reviews: Mapped[list["Review"]] = relationship(
        back_populates="submission",
        cascade="all, delete-orphan",
        order_by="Review.created_at.asc()",
    )
    comments: Mapped[list["ReviewComment"]] = relationship(
        back_populates="submission",
        cascade="all, delete-orphan",
        order_by="ReviewComment.created_at.asc()",
    )
    events: Mapped[list["ReviewEvent"]] = relationship(
        back_populates="submission",
        cascade="all, delete-orphan",
        order_by="ReviewEvent.created_at.asc()",
    )


class SubmissionFile(Base):
    """File change manifest attached to a submission."""

    __tablename__ = "submission_files"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    submission_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("submissions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    change_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=FileChangeType.MODIFIED.value,
    )
    summary: Mapped[str | None] = mapped_column(String(500), nullable=True)

    submission: Mapped[Submission] = relationship(back_populates="files")


from app.models.review import Review, ReviewComment, ReviewEvent  # noqa: E402,F401

