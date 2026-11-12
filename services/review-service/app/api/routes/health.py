"""Health and readiness endpoints for Review Service."""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from app.core.database import check_database_health
from app.schemas.common import HealthResponse, ReadinessResponse

router = APIRouter(tags=["Health"])


@router.get("/health", response_model=HealthResponse)
async def health_check(request: Request) -> HealthResponse:
    settings = request.app.state.settings
    return HealthResponse(
        status="healthy",
        service="review-service",
        version=settings.APP_VERSION,
        environment=settings.APP_ENV,
    )


@router.get("/ready", response_model=ReadinessResponse)
async def readiness_check(request: Request) -> JSONResponse | ReadinessResponse:
    db_ok = await check_database_health(request.app.state.engine)
    payload = ReadinessResponse(
        status="ready" if db_ok else "not_ready",
        service="review-service",
        database="ok" if db_ok else "unavailable",
    )
    if not db_ok:
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=payload.model_dump())
    return payload

