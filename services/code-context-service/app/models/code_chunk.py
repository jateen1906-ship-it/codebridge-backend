"""CodeChunk ORM model storing compact chunk metadata linked to ChromaDB vector IDs."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utc_now


class CodeChunk(Base):
    """Metadata record mapping repository file line spans to ChromaDB vector documents.

    Raw chunk source text and dense embeddings reside in ChromaDB rather than PostgreSQL
    to avoid duplicating large repository bodies across both stores.
    """

    __tablename__ = "code_chunks"
    __table_args__ = (
        UniqueConstraint(
            "repository_index_id",
            "chunk_identifier",
            name="uq_code_chunk_index_identifier",
        ),
        Index("ix_code_chunks_index_vector", "repository_index_id", "vector_id"),
        Index("ix_code_chunks_index_file", "repository_index_id", "indexed_file_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repository_index_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("repository_indexes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    indexed_file_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("indexed_files.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    chunk_identifier: Mapped[str] = mapped_column(String(128), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    language: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    token_estimate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    vector_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

