from fastapi import APIRouter

from app.api.routes.admin import router as admin_router
from app.api.routes.health import router as health_router


def build_root_router(api_v1_prefix: str = "/api/v1") -> APIRouter:
    root = APIRouter()
    root.include_router(health_router)
    api = APIRouter(prefix=api_v1_prefix)
    api.include_router(admin_router)
    root.include_router(api)
    return root
