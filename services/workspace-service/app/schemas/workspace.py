"""Pydantic request and response schemas for Workspace Service."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.workspace import WorkspaceStatus


class WorkspaceProvisionRequest(BaseModel):
    """Request payload for creating/provisioning an isolated workspace."""

    model_config = ConfigDict(extra="forbid")

    ref: str | None = Field(
        default=None,
        max_length=200,
        description="Optional branch, tag, or commit SHA; defaults to repository default branch.",
    )
    runtime_image: str | None = Field(
        default=None,
        max_length=128,
        description="Optional allowlisted container image.",
    )
    auto_start: bool = Field(
        default=False,
        description="Start the workspace container immediately after provisioning.",
    )

    @field_validator("runtime_image")
    @classmethod
    def _clean_image(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class WorkspaceRefreshRequest(BaseModel):
    """Request payload for refreshing repository files in an existing workspace."""

    model_config = ConfigDict(extra="forbid")

    ref: str | None = Field(default=None, max_length=200)


class WorkspaceEventResponse(BaseModel):
    """Audit event attached to a workspace."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    actor_id: uuid.UUID
    event_type: str
    previous_status: str | None = None
    new_status: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class WorkspaceResponse(BaseModel):
    """Sanitized workspace representation exposed publicly."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    owner_id: uuid.UUID
    assigned_freelancer_id: uuid.UUID | None = None
    created_by: uuid.UUID
    repository_full_name: str
    repository_ref: str
    snapshot_sha256: str | None = None
    status: WorkspaceStatus
    container_id: str | None = None
    container_name: str
    runtime_image: str
    cpu_limit: str
    memory_limit_mb: int
    pids_limit: int
    network_disabled: bool
    storage_bytes: int
    file_count: int
    last_error: str | None = None
    expires_at: datetime
    started_at: datetime | None = None
    stopped_at: datetime | None = None
    destroyed_at: datetime | None = None
    last_refreshed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class WorkspaceDetailResponse(WorkspaceResponse):
    """Workspace detail including recent lifecycle events."""

    events: list[WorkspaceEventResponse] = Field(default_factory=list)


class WorkspaceListResponse(BaseModel):
    """Paginated list of workspaces for a project."""

    items: list[WorkspaceResponse]
    total: int


class WorkspaceLogsResponse(BaseModel):
    """Sanitized bounded container log output."""

    workspace_id: uuid.UUID
    status: WorkspaceStatus
    tail: int
    lines: list[str]
    truncated: bool = False


class WorkspaceCleanupResponse(BaseModel):
    """Summary of expired workspaces cleaned up and destroyed."""

    expired_count: int
    destroyed_workspace_ids: list[uuid.UUID] = Field(default_factory=list)
    errors: int = 0


class WorkspaceTestRunRequest(BaseModel):
    """Allowlisted test execution request inside a provisioned workspace."""

    model_config = ConfigDict(extra="forbid")

    runner: Literal["pytest", "unittest", "npm_test"] = "pytest"
    target_path: str | None = Field(
        default=None,
        max_length=200,
        description="Optional safe relative directory or test file within the workspace.",
    )
    timeout_seconds: int = Field(default=30, ge=5, le=120)

    @field_validator("target_path")
    @classmethod
    def _validate_target_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().replace("\\", "/")
        if not cleaned:
            return None
        if cleaned.startswith("/") or ".." in cleaned.split("/"):
            raise ValueError("target_path must be a relative path inside the workspace.")
        for ch in cleaned:
            if not (ch.isalnum() or ch in "_.-/"):
                raise ValueError("target_path contains disallowed characters.")
        return cleaned


class WorkspaceTestRunResponse(BaseModel):
    """Structured result from executing allowlisted checks in a workspace."""

    workspace_id: uuid.UUID
    project_id: uuid.UUID
    runner: str
    passed: bool
    exit_code: int
    duration_ms: float
    summary: str
    output_excerpt: str
    executed_at: datetime

