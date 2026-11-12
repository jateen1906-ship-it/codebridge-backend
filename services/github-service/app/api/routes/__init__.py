"""Route exports for GitHub Service."""

from app.api.routes.health import router as health_router
from app.api.routes.oauth import router as oauth_router
from app.api.routes.project_github import router as project_github_router
from app.api.routes.repositories import router as repositories_router

__all__ = [
    "health_router",
    "oauth_router",
    "project_github_router",
    "repositories_router",
]
