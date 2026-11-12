"""Internal synchronous HTTP/REST clients for Identity, Project, and GitHub Services."""

from app.clients.github_client import (
    ConnectedRepositoryInfo,
    GitHubBranchInfo,
    GitHubContentNode,
    GitHubDirectoryEntry,
    GitHubServiceClient,
)
from app.clients.identity_client import IdentityClient
from app.clients.project_client import ProjectClient, ProjectSummary, assert_project_access

__all__ = [
    "ConnectedRepositoryInfo",
    "GitHubBranchInfo",
    "GitHubContentNode",
    "GitHubDirectoryEntry",
    "GitHubServiceClient",
    "IdentityClient",
    "ProjectClient",
    "ProjectSummary",
    "assert_project_access",
]
