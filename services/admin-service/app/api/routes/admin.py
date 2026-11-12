"""Strict ADMIN-only moderation & inspection endpoints mounted under /api/v1/admin."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import AuthenticatedUser, require_admin
from app.schemas.admin import (
    AdminActionResponse,
    CreateReportRequest,
    ModerationReportResponse,
    ResolveReportRequest,
    UserModerationRequest,
    UserModerationResponse,
)
from app.services.admin_service import AdminService

router = APIRouter(prefix="/admin", tags=["Administration"])


def _service(request: Request, session: AsyncSession) -> AdminService:
    return AdminService(
        session=session,
        identity_client=request.app.state.identity_client,
        inspection_client=request.app.state.inspection_client,
    )


@router.get("/users")
async def list_platform_users(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    role: str | None = Query(default=None),
    admin: AuthenticatedUser = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    return await _service(request, session).list_users(
        admin=admin,
        page=page,
        page_size=page_size,
        role=role,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/users/{user_id}/suspend", response_model=UserModerationResponse)
async def suspend_user(
    user_id: uuid.UUID,
    payload: UserModerationRequest,
    request: Request,
    admin: AuthenticatedUser = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> UserModerationResponse:
    return await _service(request, session).suspend_user(
        target_user_id=user_id,
        payload=payload,
        admin=admin,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/users/{user_id}/restore", response_model=UserModerationResponse)
async def restore_user(
    user_id: uuid.UUID,
    payload: UserModerationRequest,
    request: Request,
    admin: AuthenticatedUser = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> UserModerationResponse:
    return await _service(request, session).restore_user(
        target_user_id=user_id,
        payload=payload,
        admin=admin,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("/projects")
async def inspect_projects(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status_filter: str | None = Query(default=None, alias="status"),
    admin: AuthenticatedUser = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    return await _service(request, session).inspect_projects(
        admin=admin,
        page=page,
        page_size=page_size,
        status_filter=status_filter,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("/audit-logs")
async def inspect_audit_logs(
    request: Request,
    service_name: str | None = Query(default=None),
    project_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    admin: AuthenticatedUser = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    return await _service(request, session).inspect_audit_logs(
        admin=admin,
        service_name=service_name,
        project_id=project_id,
        limit=limit,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/reports", response_model=ModerationReportResponse, status_code=status.HTTP_201_CREATED)
async def create_moderation_report(
    payload: CreateReportRequest,
    request: Request,
    admin: AuthenticatedUser = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> ModerationReportResponse:
    return await _service(request, session).create_report(
        payload=payload,
        admin=admin,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("/reports", response_model=list[ModerationReportResponse])
async def list_moderation_reports(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status"),
    _admin: AuthenticatedUser = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> list[ModerationReportResponse]:
    return await _service(request, session).list_reports(status_filter=status_filter)


@router.post("/reports/{report_id}/resolve", response_model=ModerationReportResponse)
async def resolve_moderation_report(
    report_id: uuid.UUID,
    payload: ResolveReportRequest,
    request: Request,
    admin: AuthenticatedUser = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> ModerationReportResponse:
    return await _service(request, session).resolve_report(
        report_id=report_id,
        payload=payload,
        admin=admin,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("/actions", response_model=list[AdminActionResponse])
async def list_admin_actions(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    _admin: AuthenticatedUser = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> list[AdminActionResponse]:
    return await _service(request, session).list_actions(limit=limit)
