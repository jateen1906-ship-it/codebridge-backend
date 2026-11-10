"""API v1 route exports for Project Service."""

from app.api.routes.assignments import router as assignments_router
from app.api.routes.projects import router as projects_router

__all__ = ["assignments_router", "projects_router"]
