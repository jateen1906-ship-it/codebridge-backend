"""Health and readiness routes for GitHub Service."""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.schemas.common import HealthResponse, ReadinessResponse

router = APIRouter(tags=["Health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="GitHub Service liveness check",
    description="Lightweight process liveness probe returning service identity and version.",
)
async def health_check(request: Request) -> HealthResponse:
    settings = request.app.state.settings
    return HealthResponse(
        status="healthy",
        service="github-service",
        version=settings.APP_VERSION,
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="GitHub Service readiness check",
    description=(
        "Verifies database connectivity via a lightweight SELECT 1 query without "
        "disclosing connection strings, secrets, or internal infrastructure details."
    ),
    responses={
        200: {"model": ReadinessResponse, "description": "Service and database are ready."},
        503: {"model": ReadinessResponse, "description": "Database dependency unavailable."},
    },
)
async def readiness_check(request: Request) -> JSONResponse:
    settings = request.app.state.settings
    session_factory = request.app.state.session_factory
    try:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        payload = ReadinessResponse(
            status="not_ready",
            service="github-service",
            version=settings.APP_VERSION,
            database="unavailable",
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=payload.model_dump(mode="json"),
        )

    payload = ReadinessResponse(
        status="ready",
        service="github-service",
        version=settings.APP_VERSION,
        database="connected",
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=payload.model_dump(mode="json"),
    )

