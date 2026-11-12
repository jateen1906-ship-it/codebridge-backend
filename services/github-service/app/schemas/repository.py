"""Pydantic schemas for GitHub repository listing and project repository connection."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.security import validate_repo_full_name


class GitHubRepositoryPermissions(BaseModel):
    """GitHub repository permission flags for the authorized owner."""

    admin: bool = False
    maintain: bool = False
    push: bool = False
    pull: bool = True


class GitHubRepositorySummary(BaseModel):
    """Safe, normalized GitHub repository summary shaped from GitHub REST responses."""

    github_repository_id: int
    name: str
    full_name: str
    owner_login: str
    is_private: bool
    default_branch: str
    description: str | None = None
    html_url: str
    is_archived: bool = False
    is_fork: bool = False
    permissions: GitHubRepositoryPermissions = Field(
        default_factory=GitHubRepositoryPermissions
    )


class GitHubRepositoryListResponse(BaseModel):
    """Paginated owner GitHub repository listing."""

    items: list[GitHubRepositorySummary]
    page: int = Field(..., ge=1)
    per_page: int = Field(..., ge=1)
    has_next_page: bool = False


class ConnectRepositoryRequest(BaseModel):
    """Validated payload selecting a GitHub repository to link to a CodeBridge project."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    github_repository_id: int = Field(..., ge=1)
    full_name: str = Field(..., min_length=3, max_length=255)

    @field_validator("full_name")
    @classmethod
    def _validate_full_name(cls, value: str) -> str:
        return validate_repo_full_name(value)

