"""Verification Service API routes mounted under /api/v1/verification."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import require_authenticated, require_owner_or_admin
from app.core.security import AuthenticatedUser
from app.schemas.verification import (
    CreateVerificationRequest,
    ProjectVerificationListResponse,
    PublicVerificationResponse,
    RevokeVerificationRequest,
    VerificationCheckResponse,
    VerifiedWorkRecordResponse,
)
from app.services.verification_service import VerificationService

router = APIRouter(prefix="/verification", tags=["Verification"])


def _service(request: Request, session: AsyncSession) -> VerificationService:
    return VerificationService(
        session=session,
        settings=request.app.state.settings,
        project_client=request.app.state.project_client,
        review_client=request.app.state.review_client,
        github_client=request.app.state.github_client,
        telemetry_client=request.app.state.telemetry_client,
    )


@router.post(
    "/records",
    response_model=VerifiedWorkRecordResponse,
    status_code=status.HTTP_201_CREATED,
)
async def issue_verification_record(
    payload: CreateVerificationRequest,
    request: Request,
    actor: AuthenticatedUser = Depends(require_owner_or_admin),
    session: AsyncSession = Depends(get_db_session),
) -> VerifiedWorkRecordResponse:
    service = _service(request, session)
    return await service.issue_record(
        payload=payload,
        actor=actor,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("/public/{public_verification_id}", response_model=PublicVerificationResponse)
async def public_lookup_verification(
    public_verification_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> PublicVerificationResponse:
    service = _service(request, session)
    return await service.get_public_record(public_verification_id)


@router.get("/projects/{project_id}", response_model=ProjectVerificationListResponse)
async def list_project_verifications(
    project_id: uuid.UUID,
    request: Request,
    user: AuthenticatedUser = Depends(require_authenticated),
    session: AsyncSession = Depends(get_db_session),
) -> ProjectVerificationListResponse:
    service = _service(request, session)
    return await service.list_project_records(
        project_id=project_id,
        user=user,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("/records/{record_id}", response_model=VerifiedWorkRecordResponse)
async def get_verification_record(
    record_id: uuid.UUID,
    request: Request,
    user: AuthenticatedUser = Depends(require_authenticated),
    session: AsyncSession = Depends(get_db_session),
) -> VerifiedWorkRecordResponse:
    service = _service(request, session)
    return await service.get_record(record_id=record_id, user=user)


@router.post("/records/{record_id}/verify", response_model=VerificationCheckResponse)
async def verify_record_signature(
    record_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> VerificationCheckResponse:
    service = _service(request, session)
    return await service.verify_record(record_id=record_id)


@router.post("/records/{record_id}/revoke", response_model=VerifiedWorkRecordResponse)
async def revoke_verification_record(
    record_id: uuid.UUID,
    payload: RevokeVerificationRequest,
    request: Request,
    actor: AuthenticatedUser = Depends(require_owner_or_admin),
    session: AsyncSession = Depends(get_db_session),
) -> VerifiedWorkRecordResponse:
    service = _service(request, session)
    return await service.revoke_record(
        record_id=record_id,
        payload=payload,
        actor=actor,
        request_id=getattr(request.state, "request_id", None),
    )
