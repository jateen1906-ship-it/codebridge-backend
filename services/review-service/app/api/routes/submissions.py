"""Submission endpoints mounted under /api/v1/submissions."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import require_authenticated, require_freelancer
from app.core.security import AuthenticatedUser
from app.schemas.review import (
    CommentCreateRequest,
    ReviewCommentResponse,
    SubmissionCreateRequest,
    SubmissionListResponse,
    SubmissionResponse,
)
from app.services.review_service import ReviewService

router = APIRouter(prefix="/submissions", tags=["Submissions"])


def _service(request: Request, session: AsyncSession) -> ReviewService:
    return ReviewService(
        session=session,
        project_client=request.app.state.project_client,
        workspace_client=request.app.state.workspace_client,
        telemetry_client=request.app.state.telemetry_client,
    )


@router.post(
    "/projects/{project_id}",
    response_model=SubmissionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_submission(
    project_id: uuid.UUID,
    payload: SubmissionCreateRequest,
    request: Request,
    freelancer: AuthenticatedUser = Depends(require_freelancer),
    session: AsyncSession = Depends(get_db_session),
) -> SubmissionResponse:
    service = _service(request, session)
    return await service.create_submission(
        project_id=project_id,
        payload=payload,
        freelancer=freelancer,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("/projects/{project_id}", response_model=SubmissionListResponse)
async def list_project_submissions(
    project_id: uuid.UUID,
    request: Request,
    user: AuthenticatedUser = Depends(require_authenticated),
    session: AsyncSession = Depends(get_db_session),
) -> SubmissionListResponse:
    service = _service(request, session)
    return await service.list_submissions(
        project_id=project_id,
        user=user,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("/{submission_id}", response_model=SubmissionResponse)
async def get_submission(
    submission_id: uuid.UUID,
    request: Request,
    user: AuthenticatedUser = Depends(require_authenticated),
    session: AsyncSession = Depends(get_db_session),
) -> SubmissionResponse:
    service = _service(request, session)
    return await service.get_submission(
        submission_id=submission_id,
        user=user,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/{submission_id}/submit", response_model=SubmissionResponse)
async def submit_draft(
    submission_id: uuid.UUID,
    request: Request,
    freelancer: AuthenticatedUser = Depends(require_freelancer),
    session: AsyncSession = Depends(get_db_session),
) -> SubmissionResponse:
    service = _service(request, session)
    return await service.submit_draft(
        submission_id=submission_id,
        freelancer=freelancer,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/{submission_id}/withdraw", response_model=SubmissionResponse)
async def withdraw_submission(
    submission_id: uuid.UUID,
    request: Request,
    freelancer: AuthenticatedUser = Depends(require_freelancer),
    session: AsyncSession = Depends(get_db_session),
) -> SubmissionResponse:
    service = _service(request, session)
    return await service.withdraw_submission(
        submission_id=submission_id,
        freelancer=freelancer,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post(
    "/{submission_id}/comments",
    response_model=ReviewCommentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_submission_comment(
    submission_id: uuid.UUID,
    payload: CommentCreateRequest,
    request: Request,
    user: AuthenticatedUser = Depends(require_authenticated),
    session: AsyncSession = Depends(get_db_session),
) -> ReviewCommentResponse:
    service = _service(request, session)
    return await service.add_comment(
        submission_id=submission_id,
        payload=payload,
        user=user,
        request_id=getattr(request.state, "request_id", None),
    )

