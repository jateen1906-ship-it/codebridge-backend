"""Health and readiness endpoints for Observability Service."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import check_database_connectivity, get_db_session

router = APIRouter(tags=["Operational"])


@router.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "healthy", "service": "observability-service", "version": "1.0.0"}


@router.get("/ready")
async def readiness_check(
    response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    try:
        await check_database_connectivity(session)
        return {"status": "ready", "service": "observability-service", "database": "reachable"}
    except Exception:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not_ready", "service": "observability-service", "database": "unreachable"}
