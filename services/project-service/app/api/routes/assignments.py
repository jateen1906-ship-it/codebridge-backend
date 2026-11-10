"""Freelancer selection and project assignment route under /api/v1/projects."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.identity_client import IdentityClient
from app.core.database import get_db_session
from app.core.dependencies import get_identity_client, require_owner
from app.core.security import AuthenticatedUser, get_request_id
from app.schemas.assignment import AssignmentResponse, SelectFreelancerRequest
from app.schemas.common import ErrorResponse
from app.services.assignment_service import AssignmentService

router = APIRouter(prefix="/projects", tags=["Assignments"])


@router.post(
    "/{project_id}/select-freelancer",
    response_model=AssignmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Select and assign a verified freelancer to an OPEN project (OWNER only)",
    description=(
        "Verifies ownership, checks project is OPEN with no active assignment, validates target "
        "freelancer against Identity Service, creates an ACTIVE Assignment, and transitions "
        "project status to ASSIGNED transactionally. "
        "Authentication: Bearer access JWT. Role required: OWNER (must own project)."
    ),
    responses={
        400: {"model": ErrorResponse, "description": "Target user is not a valid freelancer."},
        401: {"model": ErrorResponse, "description": "Unauthorized."},
        403: {"model": ErrorResponse, "description": "Forbidden (OWNER and ownership required)."},
        404: {"model": ErrorResponse, "description": "Project or freelancer not found."},
        409: {
            "model": ErrorResponse,
            "description": "Project already assigned, invalid status, or concurrent conflict.",
        },
        503: {"model": ErrorResponse, "description": "Identity Service unavailable."},
    },
)
async def select_freelancer(
    project_id: uuid.UUID,
    payload: SelectFreelancerRequest,
    request: Request,
    current_owner: AuthenticatedUser = Depends(require_owner),
    session: AsyncSession = Depends(get_db_session),
    identity_client: IdentityClient = Depends(get_identity_client),
) -> AssignmentResponse:
    service = AssignmentService(session, identity_client)
    return await service.select_freelancer(
        project_id=project_id,
        owner=current_owner,
        freelancer_id=payload.freelancer_id,
        request_id=get_request_id(request),
    )

