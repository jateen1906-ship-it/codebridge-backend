"""API Gateway routes package."""

from app.routes.health import router as health_router
from app.routes.proxy import build_proxy_router

__all__ = ["health_router", "build_proxy_router"]
