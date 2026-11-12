"""Review decision and project review summary endpoints mounted under /api/v1/review."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import require_authenticated, require_owner
from app.core.security import AuthenticatedUser
from app.schemas.review import (
    ProjectReviewSummaryResponse,
    ReviewDecisionRequest,
    SubmissionResponse,
)
from app.services.review_service import ReviewService

router = APIRouter(prefix="/review", tags=["Reviews"])


def _service(request: Request, session: AsyncSession) -> ReviewService:
    return ReviewService(
        session=session,
        project_client=request.app.state.project_client,
        workspace_client=request.app.state.workspace_client,
        telemetry_client=request.app.state.telemetry_client,
    )


@router.post("/submissions/{submission_id}/start", response_model=SubmissionResponse)
async def start_submission_review(
    submission_id: uuid.UUID,
    request: Request,
    owner: AuthenticatedUser = Depends(require_owner),
    session: AsyncSession = Depends(get_db_session),
) -> SubmissionResponse:
    service = _service(request, session)
    return await service.start_review(
        submission_id=submission_id,
        owner=owner,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/submissions/{submission_id}/decision", response_model=SubmissionResponse)
async def decide_submission_review(
    submission_id: uuid.UUID,
    payload: ReviewDecisionRequest,
    request: Request,
    owner: AuthenticatedUser = Depends(require_owner),
    session: AsyncSession = Depends(get_db_session),
) -> SubmissionResponse:
    service = _service(request, session)
    return await service.decide_review(
        submission_id=submission_id,
        payload=payload,
        owner=owner,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("/submissions/{submission_id}", response_model=SubmissionResponse)
async def get_submission_review(
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


@router.get("/projects/{project_id}", response_model=ProjectReviewSummaryResponse)
async def get_project_review_summary(
    project_id: uuid.UUID,
    request: Request,
    user: AuthenticatedUser = Depends(require_authenticated),
    session: AsyncSession = Depends(get_db_session),
) -> ProjectReviewSummaryResponse:
    service = _service(request, session)
    return await service.get_project_review_summary(
        project_id=project_id,
        user=user,
        request_id=getattr(request.state, "request_id", None),
    )

