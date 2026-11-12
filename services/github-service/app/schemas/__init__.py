"""Pydantic schema exports for GitHub Service."""

from app.schemas.common import ErrorDetail, ErrorResponse, HealthResponse, ReadinessResponse
from app.schemas.oauth import GitHubConnectionDisconnectResponse, OAuthCallbackResponse
from app.schemas.project_github import (
    BranchListResponse,
    BranchSummary,
    CommitListResponse,
    CommitSummary,
    ProjectGitHubDisconnectResponse,
    ProjectGitHubStatusResponse,
    ProjectRepositoryMetadata,
    RepositoryContentEntry,
    RepositoryContentsResponse,
)
from app.schemas.repository import (
    ConnectRepositoryRequest,
    GitHubRepositoryListResponse,
    GitHubRepositoryPermissions,
    GitHubRepositorySummary,
)

__all__ = [
    "BranchListResponse",
    "BranchSummary",
    "CommitListResponse",
    "CommitSummary",
    "ConnectRepositoryRequest",
    "ErrorDetail",
    "ErrorResponse",
    "GitHubConnectionDisconnectResponse",
    "GitHubRepositoryListResponse",
    "GitHubRepositoryPermissions",
    "GitHubRepositorySummary",
    "HealthResponse",
    "OAuthCallbackResponse",
    "ProjectGitHubDisconnectResponse",
    "ProjectGitHubStatusResponse",
    "ProjectRepositoryMetadata",
    "ReadinessResponse",
    "RepositoryContentEntry",
    "RepositoryContentsResponse",
]
