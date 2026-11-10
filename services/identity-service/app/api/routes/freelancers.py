"""Public freelancer discovery and FREELANCER self-management routes under /api/v1/freelancers."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import require_freelancer
from app.models.freelancer_profile import AvailabilityStatus
from app.models.user import User
from app.schemas.common import ErrorResponse
from app.schemas.freelancer import (
    FreelancerListResponse,
    FreelancerProfileDetail,
    FreelancerProfileUpdateRequest,
    FreelancerPublicResponse,
)
from app.services.profile_service import ProfileService

router = APIRouter(prefix="/freelancers", tags=["Freelancers"])


@router.get(
    "",
    response_model=FreelancerListResponse,
    status_code=status.HTTP_200_OK,
    summary="List public freelancer profiles",
    description=(
        "Supports pagination, name search, skill filtering, and availability filtering. "
        "Returns public freelancer information only (never exposes emails or credentials). "
        "Authentication: None (Public)."
    ),
)
async def list_freelancers(
    page: int = Query(1, ge=1, description="Page number (1-indexed)."),
    page_size: int = Query(20, ge=1, le=100, description="Items per page (max 100)."),
    search: str | None = Query(None, max_length=100, description="Case-insensitive name search."),
    skill: str | None = Query(None, max_length=50, description="Filter by exact skill tag."),
    availability: AvailabilityStatus | None = Query(None, description="Filter by availability."),
    session: AsyncSession = Depends(get_db_session),
) -> FreelancerListResponse:
    service = ProfileService(session)
    return await service.list_public_freelancers(
        page=page,
        page_size=page_size,
        search=search,
        skill=skill,
        availability=availability,
    )


@router.patch(
    "/me",
    response_model=FreelancerProfileDetail,
    status_code=status.HTTP_200_OK,
    summary="Update authenticated FREELANCER's profile",
    description=(
        "Allows an authenticated FREELANCER to update their bio, skills, experience_years, "
        "and availability. Authentication: Bearer access JWT. Role required: FREELANCER."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized."},
        403: {"model": ErrorResponse, "description": "Forbidden (FREELANCER role required)."},
        422: {"model": ErrorResponse, "description": "Validation error."},
    },
)
async def update_my_freelancer_profile(
    payload: FreelancerProfileUpdateRequest,
    current_user: User = Depends(require_freelancer),
    session: AsyncSession = Depends(get_db_session),
) -> FreelancerProfileDetail:
    service = ProfileService(session)
    profile = await service.update_my_freelancer_profile(
        user=current_user,
        payload=payload,
    )
    return FreelancerProfileDetail.model_validate(profile)


@router.get(
    "/{freelancer_id}",
    response_model=FreelancerPublicResponse,
    status_code=status.HTTP_200_OK,
    summary="Get public freelancer profile by ID",
    description=(
        "Retrieves a single active freelancer's public profile by profile ID or user ID. "
        "Authentication: None (Public)."
    ),
    responses={
        404: {"model": ErrorResponse, "description": "Freelancer profile not found."},
        422: {"model": ErrorResponse, "description": "Invalid UUID format."},
    },
)
async def get_freelancer_profile(
    freelancer_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> FreelancerPublicResponse:
    service = ProfileService(session)
    return await service.get_public_freelancer(freelancer_id)

