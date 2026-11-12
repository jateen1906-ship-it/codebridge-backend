from fastapi import APIRouter

from app.api.routes.health import router as health_router
from app.api.routes.observability import router as observability_router


def build_root_router(api_v1_prefix: str = "/api/v1") -> APIRouter:
    root = APIRouter()
    root.include_router(health_router)
    root.include_router(observability_router, prefix=api_v1_prefix)
    return root
