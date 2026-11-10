"""Identity Service Pydantic schemas package."""

from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    RefreshTokenRequest,
    RegisterRequest,
    TokenPairResponse,
)
from app.schemas.common import ErrorDetail, ErrorResponse, HealthResponse, MessageResponse
from app.schemas.freelancer import (
    FreelancerListResponse,
    FreelancerProfileDetail,
    FreelancerProfileUpdateRequest,
    FreelancerPublicResponse,
)
from app.schemas.profile import ProfileResponse, ProfileUpdateRequest
from app.schemas.user import (
    AdminUserCreateRequest,
    AdminUserListResponse,
    AdminUserStatusUpdateRequest,
    UserPublicResponse,
)

__all__ = [
    "AdminUserCreateRequest",
    "AdminUserListResponse",
    "AdminUserStatusUpdateRequest",
    "ErrorDetail",
    "ErrorResponse",
    "FreelancerListResponse",
    "FreelancerProfileDetail",
    "FreelancerProfileUpdateRequest",
    "FreelancerPublicResponse",
    "HealthResponse",
    "LoginRequest",
    "LogoutRequest",
    "MessageResponse",
    "ProfileResponse",
    "ProfileUpdateRequest",
    "RefreshTokenRequest",
    "RegisterRequest",
    "TokenPairResponse",
    "UserPublicResponse",
]
