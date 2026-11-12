"""Pydantic schemas for project GitHub status, disconnection, and read-only repo access."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.project_repository import ProjectRepositoryStatus


class ProjectRepositoryMetadata(BaseModel):
    """Public/project-authorized metadata describing a connected GitHub repository."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    github_repository_id: int
    full_name: str
    repository_name: str
    owner_login: str
    is_private: bool
    default_branch: str
    html_url: str
    description: str | None = None
    is_archived: bool
    is_fork: bool
    connection_status: ProjectRepositoryStatus
    connected_at: datetime
    updated_at: datetime


class ProjectGitHubStatusResponse(BaseModel):
    """Project GitHub connection status returned by GET /projects/{project_id}/github/status."""

    project_id: uuid.UUID
    connected: bool
    connection_status: Literal["ACTIVE", "NOT_CONNECTED"]
    repository: ProjectRepositoryMetadata | None = None


class ProjectGitHubDisconnectResponse(BaseModel):
    """Response returned when disconnecting a repository from a single CodeBridge project."""

    success: Literal[True] = True
    project_id: uuid.UUID
    connection_status: Literal["DISCONNECTED"] = "DISCONNECTED"
    disconnected_at: datetime


class BranchSummary(BaseModel):
    """Read-only summary of a repository branch."""

    name: str
    commit_sha: str
    protected: bool = False


class BranchListResponse(BaseModel):
    """Paginated branch list for a connected project repository."""

    project_id: uuid.UUID
    full_name: str
    items: list[BranchSummary]
    page: int = Field(..., ge=1)
    per_page: int = Field(..., ge=1)
    has_next_page: bool = False


class RepositoryContentEntry(BaseModel):
    """Directory entry metadata returned when reading a GitHub directory path."""

    name: str
    path: str
    type: Literal["file", "dir", "symlink", "submodule", "other"]
    size: int = 0
    sha: str


class RepositoryContentsResponse(BaseModel):
    """Read-only file content or directory listing response with size/entry safety bounds."""

    project_id: uuid.UUID
    full_name: str
    ref: str | None = None
    path: str
    kind: Literal["file", "dir"]
    sha: str | None = None
    size: int | None = None
    encoding: str | None = None
    is_binary: bool = False
    truncated: bool = False
    content: str | None = None
    entries: list[RepositoryContentEntry] | None = None


class CommitSummary(BaseModel):
    """Read-only summary of a GitHub repository commit."""

    sha: str
    short_sha: str
    message: str
    author_name: str | None = None
    author_login: str | None = None
    committed_at: datetime | None = None
    html_url: str | None = None


class CommitListResponse(BaseModel):
    """Paginated commit history for a connected project repository."""

    project_id: uuid.UUID
    full_name: str
    items: list[CommitSummary]
    page: int = Field(..., ge=1)
    per_page: int = Field(..., ge=1)
    has_next_page: bool = False

