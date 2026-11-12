"""Observability API endpoints mounted under /api/v1/observability."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import (
    AuthenticatedUser,
    require_admin,
    require_internal_or_admin,
)
from app.schemas.observability import (
    ErrorLogListResponse,
    PlatformMetricsResponse,
    PlatformServicesStatusResponse,
    TelemetryCreateRequest,
    TelemetryRecordResponse,
)
from app.services.observability_service import ObservabilityService

router = APIRouter(prefix="/observability", tags=["Observability"])


def _service(request: Request, session: AsyncSession) -> ObservabilityService:
    return ObservabilityService(session=session, probe_client=request.app.state.probe_client)


@router.get("/services", response_model=PlatformServicesStatusResponse)
async def get_platform_services_status(
    request: Request,
    _admin: AuthenticatedUser = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> PlatformServicesStatusResponse:
    return await _service(request, session).get_platform_services_status(
        request_id=getattr(request.state, "request_id", None),
    )


@router.post(
    "/telemetry",
    response_model=TelemetryRecordResponse,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_telemetry_record(
    payload: TelemetryCreateRequest,
    request: Request,
    _caller: str = Depends(require_internal_or_admin),
    session: AsyncSession = Depends(get_db_session),
) -> TelemetryRecordResponse:
    return await _service(request, session).record_telemetry(payload=payload)


@router.get("/metrics", response_model=PlatformMetricsResponse)
async def get_platform_metrics(
    request: Request,
    service_name: str | None = Query(default=None),
    _admin: AuthenticatedUser = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> PlatformMetricsResponse:
    return await _service(request, session).get_metrics_summary(service_name=service_name)


@router.get("/errors", response_model=ErrorLogListResponse)
async def get_platform_errors(
    request: Request,
    service_name: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    _admin: AuthenticatedUser = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> ErrorLogListResponse:
    return await _service(request, session).list_recent_errors(
        service_name=service_name,
        limit=limit,
    )
