"""API Gateway self-health endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["Health"])


@router.get("/health", summary="API Gateway health check")
async def health_check(request: Request) -> dict[str, str]:
    """Check the health of the API Gateway process itself."""
    settings = request.app.state.settings
    return {
        "status": "healthy",
        "service": "api-gateway",
        "version": settings.APP_VERSION,
    }

