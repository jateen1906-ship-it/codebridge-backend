"""Identity Service API routes package."""

from app.api.routes.auth import router as auth_router
from app.api.routes.freelancers import router as freelancers_router
from app.api.routes.profile import router as profile_router

__all__ = ["auth_router", "freelancers_router", "profile_router"]
