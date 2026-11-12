"""RepositoryInsight ORM model persisting deterministic health & AI summary reports."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JSONVariant, utc_now


class RepositoryInsight(Base):
    """Persisted deterministic repository diagnostics and optional labeled AI summary."""

    __tablename__ = "repository_insights"
    __table_args__ = (
        Index("ix_repo_insights_project_generated", "project_id", "generated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    repository_index_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("repository_indexes.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    index_version: Mapped[int] = mapped_column(Integer, nullable=False)
    deterministic_report: Mapped[dict[str, Any]] = mapped_column(JSONVariant, nullable=False)
    ai_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONVariant, nullable=True)
    ai_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    llm_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    llm_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    generated_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

