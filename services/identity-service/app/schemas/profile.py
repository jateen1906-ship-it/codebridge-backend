"""Authenticated user profile read and permitted-update schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.user import UserRole
from app.schemas.freelancer import FreelancerProfileDetail


class ProfileResponse(BaseModel):
    """Authenticated user's profile including freelancer details when applicable."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    email: EmailStr
    role: UserRole
    is_active: bool
    is_verified: bool
    created_at: datetime
    updated_at: datetime
    freelancer_profile: FreelancerProfileDetail | None = None


class ProfileUpdateRequest(BaseModel):
    """Strictly permitted mutable profile fields (forbids role, id, active, verified)."""

    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(..., min_length=2, max_length=120)

    @field_validator("full_name")
    @classmethod
    def _clean_full_name(cls, value: str) -> str:
        cleaned = " ".join(value.strip().split())
        if len(cleaned) < 2:
            raise ValueError("full_name must contain at least 2 non-space characters.")
        return cleaned

