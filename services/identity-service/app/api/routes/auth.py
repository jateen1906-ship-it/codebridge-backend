"""Authentication and restricted administrative identity routes under /api/v1/auth."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.database import get_db_session
from app.core.dependencies import get_current_user, get_settings, require_admin
from app.models.user import User, UserRole
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    RefreshTokenRequest,
    RegisterRequest,
    TokenPairResponse,
)
from app.schemas.common import ErrorResponse, MessageResponse
from app.schemas.user import (
    AdminUserCreateRequest,
    AdminUserListResponse,
    AdminUserStatusUpdateRequest,
    UserPublicResponse,
)
from app.services.auth_service import AuthService
from app.services.user_service import UserService

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/register",
    response_model=UserPublicResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new OWNER or FREELANCER user",
    description=(
        "Public registration endpoint. Authentication: None. "
        "Allowed roles: OWNER or FREELANCER only (ADMIN registration is strictly rejected). "
        "Automatically initializes an empty FreelancerProfile when role is FREELANCER."
    ),
    responses={
        403: {"model": ErrorResponse, "description": "Attempted public ADMIN registration."},
        409: {"model": ErrorResponse, "description": "Email already registered."},
        422: {"model": ErrorResponse, "description": "Validation error."},
    },
)
async def register_user(
    payload: RegisterRequest,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> UserPublicResponse:
    service = AuthService(session, settings)
    user = await service.register(payload)
    return UserPublicResponse.model_validate(user)


@router.post(
    "/login",
    response_model=TokenPairResponse,
    status_code=status.HTTP_200_OK,
    summary="Authenticate user and issue access + refresh JWTs",
    description=(
        "Validates email and Argon2 password hash, rejects inactive users, "
        "and returns signed access and refresh tokens with expiration metadata. "
        "Authentication: None (Public)."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Invalid credentials."},
        403: {"model": ErrorResponse, "description": "User account is inactive."},
        422: {"model": ErrorResponse, "description": "Validation error."},
    },
)
async def login_user(
    payload: LoginRequest,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> TokenPairResponse:
    service = AuthService(session, settings)
    return await service.login(payload)


@router.post(
    "/refresh",
    response_model=TokenPairResponse,
    status_code=status.HTTP_200_OK,
    summary="Rotate refresh token and issue a fresh token pair",
    description=(
        "Validates refresh JWT signature/expiration and hashed JTI DB record, "
        "performs transactional rotation, revokes the previous token, and revokes the "
        "token family if reuse is detected. Authentication: Valid refresh token."
    ),
    responses={
        401: {
            "model": ErrorResponse,
            "description": "Invalid, expired, or revoked/reused refresh token.",
        },
        403: {"model": ErrorResponse, "description": "Account is inactive."},
        422: {"model": ErrorResponse, "description": "Validation error."},
    },
)
async def refresh_tokens(
    payload: RefreshTokenRequest,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> TokenPairResponse:
    service = AuthService(session, settings)
    return await service.refresh(payload.refresh_token)


@router.post(
    "/logout",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Logout and revoke refresh token session",
    description=(
        "Revokes the authenticated user's refresh-token session so it cannot be reused. "
        "Authentication: Bearer access JWT (Any active role)."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Missing/invalid/revoked token."},
        403: {"model": ErrorResponse, "description": "Token does not belong to caller."},
    },
)
async def logout_user(
    payload: LogoutRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> MessageResponse:
    service = AuthService(session, settings)
    await service.logout(
        refresh_token=payload.refresh_token,
        authenticated_user_id=current_user.id,
    )
    return MessageResponse(message="Logged out successfully.")


@router.get(
    "/me",
    response_model=UserPublicResponse,
    status_code=status.HTTP_200_OK,
    summary="Get current authenticated user identity",
    description=(
        "Returns the authenticated user's public identity record. "
        "Authentication: Bearer access JWT (OWNER, FREELANCER, or ADMIN)."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized or invalid/expired token."},
        403: {"model": ErrorResponse, "description": "Inactive account."},
    },
)
async def get_me(current_user: User = Depends(get_current_user)) -> UserPublicResponse:
    return UserPublicResponse.model_validate(current_user)


@router.post(
    "/admin/users",
    response_model=UserPublicResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Admin Identity Operations"],
    summary="Provision a user or ADMIN account (ADMIN only)",
    description=(
        "Restricted administrative account provisioning endpoint. "
        "Authentication: Bearer access JWT. Role required: ADMIN."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized."},
        403: {"model": ErrorResponse, "description": "Forbidden (ADMIN role required)."},
        409: {"model": ErrorResponse, "description": "Email already exists."},
    },
)
async def admin_provision_user(
    payload: AdminUserCreateRequest,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> UserPublicResponse:
    service = UserService(session)
    user = await service.provision_user_by_admin(payload)
    return UserPublicResponse.model_validate(user)


@router.get(
    "/admin/users",
    response_model=AdminUserListResponse,
    status_code=status.HTTP_200_OK,
    tags=["Admin Identity Operations"],
    summary="List identity accounts (ADMIN only)",
    description=(
        "Paginated listing of identity accounts for administrative review. "
        "Authentication: Bearer access JWT. Role required: ADMIN."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized."},
        403: {"model": ErrorResponse, "description": "Forbidden (ADMIN role required)."},
    },
)
async def admin_list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    role: UserRole | None = Query(None),
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> AdminUserListResponse:
    service = UserService(session)
    return await service.list_users_for_admin(page=page, page_size=page_size, role=role)


@router.patch(
    "/admin/users/{user_id}/status",
    response_model=UserPublicResponse,
    status_code=status.HTTP_200_OK,
    tags=["Admin Identity Operations"],
    summary="Update user active/verified status (ADMIN only)",
    description=(
        "Administrative endpoint to activate/deactivate or verify an account. "
        "Deactivating a user immediately revokes all their active refresh tokens. "
        "Authentication: Bearer access JWT. Role required: ADMIN."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized."},
        403: {"model": ErrorResponse, "description": "Forbidden (ADMIN role required)."},
        404: {"model": ErrorResponse, "description": "User not found."},
    },
)
async def admin_update_user_status(
    user_id: uuid.UUID,
    payload: AdminUserStatusUpdateRequest,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> UserPublicResponse:
    service = UserService(session)
    user = await service.update_user_status_by_admin(user_id=user_id, payload=payload)
    return UserPublicResponse.model_validate(user)

