"""Authentication request and token pair response schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.user import UserRole
from app.schemas.user import UserPublicResponse


class RegisterRequest(BaseModel):
    """Public account registration payload (OWNER or FREELANCER only)."""

    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(..., min_length=2, max_length=120, examples=["Demo User"])
    email: EmailStr = Field(..., examples=["demo@example.com"])
    password: str = Field(..., min_length=8, max_length=128, examples=["StrongPassword123!"])
    role: UserRole = Field(..., examples=[UserRole.FREELANCER])

    @field_validator("full_name")
    @classmethod
    def _clean_full_name(cls, value: str) -> str:
        cleaned = " ".join(value.strip().split())
        if len(cleaned) < 2:
            raise ValueError("full_name must contain at least 2 non-space characters.")
        return cleaned

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()

    @field_validator("password")
    @classmethod
    def _validate_password_strength(cls, value: str) -> str:
        has_upper = any(ch.isupper() for ch in value)
        has_lower = any(ch.islower() for ch in value)
        has_digit = any(ch.isdigit() for ch in value)
        has_special = any(not ch.isalnum() for ch in value)
        if not (has_upper and has_lower and has_digit and has_special):
            raise ValueError(
                "Password must contain uppercase, lowercase, numeric, and special characters."
            )
        return value


class LoginRequest(BaseModel):
    """User login credentials payload."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(..., examples=["demo@example.com"])
    password: str = Field(..., min_length=1, max_length=128, examples=["StrongPassword123!"])

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()


class RefreshTokenRequest(BaseModel):
    """Refresh token rotation request payload."""

    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(..., min_length=20, max_length=4096)


class LogoutRequest(BaseModel):
    """Logout payload identifying the refresh token to revoke."""

    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(..., min_length=20, max_length=4096)


class TokenPairResponse(BaseModel):
    """Signed access and refresh token pair with expiration metadata."""

    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(..., description="Access token lifetime in seconds.")
    refresh_expires_in: int = Field(..., description="Refresh token lifetime in seconds.")
    user: UserPublicResponse

