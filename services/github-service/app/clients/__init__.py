"""Service-to-service and GitHub HTTPX client exports."""

from app.clients.github_client import GitHubHTTPClient
from app.clients.identity_client import IdentityClient
from app.clients.project_client import ProjectClient, ProjectSummary

__all__ = [
    "GitHubHTTPClient",
    "IdentityClient",
    "ProjectClient",
    "ProjectSummary",
]
