"""Pydantic request and response schemas for submissions, reviews, and comments."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _clean_text(value: str, *, field_name: str, min_len: int = 1) -> str:
    cleaned = value.strip()
    if len(cleaned) < min_len:
        raise ValueError(f"{field_name} must be at least {min_len} characters.")
    return cleaned


class SubmissionFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_path: str = Field(..., min_length=1, max_length=500)
    change_type: Literal["ADDED", "MODIFIED", "DELETED"] = "MODIFIED"
    summary: str | None = Field(default=None, max_length=500)

    @field_validator("file_path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        cleaned = value.strip().replace("\\", "/")
        if not cleaned or cleaned.startswith("/") or ".." in cleaned.split("/"):
            raise ValueError("file_path must be a safe relative repository path.")
        return cleaned


class SubmissionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: uuid.UUID | None = None
    title: str = Field(..., min_length=3, max_length=200)
    summary: str = Field(..., min_length=5, max_length=5000)
    changes_summary: str = Field(..., min_length=5, max_length=10000)
    test_results_summary: str | None = Field(default=None, max_length=5000)
    git_ref: str | None = Field(default=None, max_length=255)
    commit_sha: str | None = Field(default=None, max_length=64)
    submit_immediately: bool = True
    files: list[SubmissionFileInput] = Field(default_factory=list, max_length=200)

    @field_validator("title")
    @classmethod
    def _validate_title(cls, value: str) -> str:
        return _clean_text(value, field_name="title", min_len=3)

    @field_validator("summary")
    @classmethod
    def _validate_summary(cls, value: str) -> str:
        return _clean_text(value, field_name="summary", min_len=5)

    @field_validator("changes_summary")
    @classmethod
    def _validate_changes(cls, value: str) -> str:
        return _clean_text(value, field_name="changes_summary", min_len=5)


class CommentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(..., min_length=1, max_length=4000)
    file_path: str | None = Field(default=None, max_length=500)
    line_number: int | None = Field(default=None, ge=1, le=1_000_000)

    @field_validator("body")
    @classmethod
    def _validate_body(cls, value: str) -> str:
        return _clean_text(value, field_name="body", min_len=1)


class ReviewDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["APPROVED", "CHANGES_REQUESTED", "REJECTED"]
    feedback: str = Field(..., min_length=3, max_length=5000)
    checklist_passed: bool = True

    @field_validator("feedback")
    @classmethod
    def _validate_feedback(cls, value: str) -> str:
        return _clean_text(value, field_name="feedback", min_len=3)


class SubmissionFileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    file_path: str
    change_type: str
    summary: str | None = None


class ReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    submission_id: uuid.UUID
    project_id: uuid.UUID
    reviewer_id: uuid.UUID
    decision: str
    feedback: str
    checklist_passed: bool
    started_at: datetime
    decided_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ReviewCommentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    submission_id: uuid.UUID
    author_id: uuid.UUID
    author_role: str
    file_path: str | None = None
    line_number: int | None = None
    body: str
    created_at: datetime


class ReviewEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    submission_id: uuid.UUID
    project_id: uuid.UUID
    actor_id: uuid.UUID
    event_type: str
    from_status: str | None = None
    to_status: str | None = None
    detail: str | None = None
    request_id: str | None = None
    created_at: datetime


class SubmissionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    freelancer_id: uuid.UUID
    owner_id: uuid.UUID
    workspace_id: uuid.UUID | None = None
    version_number: int
    title: str
    summary: str
    changes_summary: str
    test_results_summary: str | None = None
    git_ref: str | None = None
    commit_sha: str | None = None
    status: str
    submitted_at: datetime | None = None
    reviewed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    files: list[SubmissionFileResponse] = Field(default_factory=list)
    reviews: list[ReviewResponse] = Field(default_factory=list)
    comments: list[ReviewCommentResponse] = Field(default_factory=list)
    events: list[ReviewEventResponse] = Field(default_factory=list)


class SubmissionListResponse(BaseModel):
    project_id: uuid.UUID
    total: int
    items: list[SubmissionResponse]


class ProjectReviewSummaryResponse(BaseModel):
    project_id: uuid.UUID
    owner_id: uuid.UUID | None = None
    approved: bool
    approved_submission_id: uuid.UUID | None = None
    approved_freelancer_id: uuid.UUID | None = None
    approved_at: datetime | None = None
    latest_submission_id: uuid.UUID | None = None
    latest_status: str | None = None
    submissions_count: int
    items: list[SubmissionResponse]

