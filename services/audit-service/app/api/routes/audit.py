"""Append-only Audit endpoints mounted under /api/v1/audit (no PUT/PATCH/DELETE allowed)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import (
    AuthenticatedUser,
    get_current_user,
    require_internal_or_admin,
)
from app.schemas.audit import (
    AuditEventCreateRequest,
    AuditLogListResponse,
    AuditLogResponse,
)
from app.services.audit_service import AuditService

router = APIRouter(prefix="/audit", tags=["Audit"])


def _service(request: Request, session: AsyncSession) -> AuditService:
    return AuditService(session=session, project_client=request.app.state.project_client)


@router.post("/events", response_model=AuditLogResponse, status_code=status.HTTP_201_CREATED)
async def append_audit_event(
    payload: AuditEventCreateRequest,
    request: Request,
    _caller: str = Depends(require_internal_or_admin),
    session: AsyncSession = Depends(get_db_session),
) -> AuditLogResponse:
    return await _service(request, session).record_event(
        payload=payload,
        fallback_request_id=getattr(request.state, "request_id", None),
    )


@router.get("/events", response_model=AuditLogListResponse)
async def list_audit_events(
    request: Request,
    service_name: str | None = Query(default=None),
    actor_id: uuid.UUID | None = Query(default=None),
    project_id: uuid.UUID | None = Query(default=None),
    action: str | None = Query(default=None),
    outcome: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> AuditLogListResponse:
    return await _service(request, session).query_events(
        user=user,
        service_name=service_name,
        actor_id=actor_id,
        project_id=project_id,
        action=action,
        outcome=outcome,
        limit=limit,
        offset=offset,
    )


@router.get("/projects/{project_id}", response_model=AuditLogListResponse)
async def list_project_audit_events(
    project_id: uuid.UUID,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> AuditLogListResponse:
    return await _service(request, session).query_project_events(
        project_id=project_id,
        user=user,
        limit=limit,
        request_id=getattr(request.state, "request_id", None),
    )
