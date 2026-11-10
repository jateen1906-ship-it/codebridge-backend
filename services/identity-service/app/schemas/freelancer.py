"""Freelancer profile request and public response schemas (no private auth/email data)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.freelancer_profile import AvailabilityStatus


class FreelancerProfileDetail(BaseModel):
    """Freelancer-specific profile attributes."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    bio: str | None = None
    skills: list[str]
    experience_years: int
    availability: AvailabilityStatus
    created_at: datetime
    updated_at: datetime


class FreelancerPublicResponse(BaseModel):
    """Publicly visible freelancer listing/detail item (omits email and auth fields)."""

    id: uuid.UUID
    user_id: uuid.UUID
    full_name: str
    bio: str | None = None
    skills: list[str]
    experience_years: int
    availability: AvailabilityStatus
    is_verified: bool
    created_at: datetime
    updated_at: datetime


class FreelancerListResponse(BaseModel):
    """Paginated list of public freelancer profiles."""

    items: list[FreelancerPublicResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class FreelancerProfileUpdateRequest(BaseModel):
    """Allowed freelancer profile fields updatable by the authenticated FREELANCER."""

    model_config = ConfigDict(extra="forbid")

    bio: str | None = Field(default=None, max_length=2000)
    skills: list[str] | None = Field(default=None, max_length=50)
    experience_years: int | None = Field(default=None, ge=0, le=70)
    availability: AvailabilityStatus | None = None

    @field_validator("bio")
    @classmethod
    def _clean_bio(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()

    @field_validator("skills")
    @classmethod
    def _normalize_skills(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            tag = " ".join(item.strip().split())
            if not tag:
                continue
            if len(tag) > 50:
                raise ValueError("Each skill must be at most 50 characters.")
            key = tag.lower()
            if key not in seen:
                seen.add(key)
                cleaned.append(tag)
        return cleaned

