"""API router exports for Code Context Service."""

from app.api.routes.health import router as health_router
from app.api.routes.indexing import router as indexing_router
from app.api.routes.insights import router as insights_router
from app.api.routes.query import router as query_router

__all__ = [
    "health_router",
    "indexing_router",
    "insights_router",
    "query_router",
]
