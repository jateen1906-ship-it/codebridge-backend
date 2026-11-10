"""Authenticated user profile routes under /api/v1/profile."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import get_current_user
from app.models.user import User
from app.schemas.common import ErrorResponse
from app.schemas.profile import ProfileResponse, ProfileUpdateRequest
from app.services.profile_service import ProfileService

router = APIRouter(prefix="/profile", tags=["Profile"])


@router.get(
    "",
    response_model=ProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Get authenticated user profile",
    description=(
        "Returns the authenticated user's profile (including freelancer profile details when applicable). "
        "Authentication: Bearer access JWT (OWNER, FREELANCER, or ADMIN)."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized or invalid token."},
        403: {"model": ErrorResponse, "description": "Account is inactive."},
    },
)
async def get_my_profile(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ProfileResponse:
    service = ProfileService(session)
    user = await service.get_authenticated_profile(current_user.id)
    return ProfileResponse.model_validate(user)


@router.patch(
    "",
    response_model=ProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Update permitted authenticated user profile fields",
    description=(
        "Updates permitted profile fields (`full_name`) for the authenticated user. "
        "Strictly rejects attempts to modify `role`, `id`, `is_verified`, or `is_active`. "
        "Authentication: Bearer access JWT (OWNER, FREELANCER, or ADMIN)."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized."},
        403: {"model": ErrorResponse, "description": "Account is inactive."},
        422: {"model": ErrorResponse, "description": "Validation or forbidden field error."},
    },
)
async def update_my_profile(
    payload: ProfileUpdateRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ProfileResponse:
    service = ProfileService(session)
    user = await service.update_authenticated_profile(
        user_id=current_user.id,
        payload=payload,
    )
    return ProfileResponse.model_validate(user)

