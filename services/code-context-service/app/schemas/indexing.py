"""Pydantic schemas for repository indexing, reindexing, and index status responses."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_SAFE_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/\-]+$")


class IndexRepositoryRequest(BaseModel):
    """Validated request body for POST /index and POST /reindex."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    branch: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Optional branch override. Defaults to connected repository default branch.",
    )

    @field_validator("branch")
    @classmethod
    def _validate_branch(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if (
            not cleaned
            or ".." in cleaned
            or cleaned.startswith(("/", "."))
            or cleaned.endswith(("/", ".lock"))
            or not _SAFE_BRANCH_RE.match(cleaned)
        ):
            raise ValueError("Invalid Git branch reference.")
        return cleaned


class IndexingJobSummary(BaseModel):
    """Summary of an indexing or reindexing job execution."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    repository_index_id: uuid.UUID
    job_type: str
    status: str
    branch: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    files_processed: int
    files_skipped: int
    chunks_generated: int
    error_code: str | None = None
    error_message: str | None = None


class IndexMetadataResponse(BaseModel):
    """Versioned repository index metadata."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    github_repository_id: int | None = None
    repository_full_name: str
    branch: str
    commit_sha: str | None = None
    index_version: int
    embedding_provider: str
    embedding_model: str
    status: str
    is_active: bool
    files_discovered: int
    files_indexed: int
    files_skipped: int
    chunks_generated: int
    total_bytes_indexed: int
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class IndexingActionResponse(BaseModel):
    """Response returned after completing POST /index or POST /reindex."""

    success: Literal[True] = True
    project_id: uuid.UUID
    job: IndexingJobSummary
    index: IndexMetadataResponse
    skipped_reasons: dict[str, int] = Field(default_factory=dict)
    request_id: str


class IndexStatusResponse(BaseModel):
    """Detailed repository indexing status & file/language summary for a project."""

    project_id: uuid.UUID
    has_active_index: bool
    index_status: str
    active_index: IndexMetadataResponse | None = None
    latest_index: IndexMetadataResponse | None = None
    latest_job: IndexingJobSummary | None = None
    skipped_reasons: dict[str, int] = Field(default_factory=dict)
    indexed_languages: dict[str, int] = Field(default_factory=dict)

