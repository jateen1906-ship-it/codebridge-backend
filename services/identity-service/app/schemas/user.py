"""User identity and restricted administrative user management schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.user import UserRole


class UserPublicResponse(BaseModel):
    """Authenticated user identity data (never includes password hashes or tokens)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    email: EmailStr
    role: UserRole
    is_active: bool
    is_verified: bool
    created_at: datetime
    updated_at: datetime


class AdminUserCreateRequest(BaseModel):
    """Restricted payload used only by ADMIN operators to provision accounts."""

    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(..., min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    role: UserRole
    is_verified: bool = True

    @field_validator("full_name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        cleaned = " ".join(value.strip().split())
        if len(cleaned) < 2:
            raise ValueError("full_name must be at least 2 characters.")
        return cleaned

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()

    @field_validator("password")
    @classmethod
    def _validate_password_strength(cls, value: str) -> str:
        if (
            len(value) < 8
            or not any(ch.isupper() for ch in value)
            or not any(ch.islower() for ch in value)
            or not any(ch.isdigit() for ch in value)
            or not any(not ch.isalnum() for ch in value)
        ):
            raise ValueError(
                "Password must be 8+ characters and include upper, lower, digit, and special character."
            )
        return value


class AdminUserStatusUpdateRequest(BaseModel):
    """Restricted administrative payload for updating user active/verification flags."""

    model_config = ConfigDict(extra="forbid")

    is_active: bool | None = None
    is_verified: bool | None = None


class AdminUserListResponse(BaseModel):
    """Paginated list of users for administrative inspection."""

    items: list[UserPublicResponse]
    total: int
    page: int
    page_size: int
    total_pages: int

