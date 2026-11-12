"""Workspace Service API route exports."""

from app.api.routes.health import router as health_router
from app.api.routes.workspaces import router as workspaces_router

__all__ = ["health_router", "workspaces_router"]
