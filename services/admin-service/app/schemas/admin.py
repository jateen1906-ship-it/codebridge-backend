"""Pydantic schemas for Admin Service."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    environment: str


class ReadinessResponse(BaseModel):
    status: str
    service: str
    database: str


class UserModerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=5, max_length=1000)

    @field_validator("reason")
    @classmethod
    def _clean(cls, v: str) -> str:
        c = v.strip()
        if len(c) < 5:
            raise ValueError("reason must be at least 5 characters.")
        return c


class CreateReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_type: Literal["USER", "PROJECT", "SUBMISSION", "OTHER"]
    target_id: uuid.UUID
    reason: str = Field(..., min_length=5, max_length=2000)
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "MEDIUM"


class ResolveReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["RESOLVED", "DISMISSED"] = "RESOLVED"
    resolution_notes: str = Field(..., min_length=5, max_length=2000)


class ModerationReportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    reporter_id: uuid.UUID
    target_type: str
    target_id: uuid.UUID
    reason: str
    severity: str
    status: str
    resolution_notes: str | None = None
    resolved_by: uuid.UUID | None = None
    resolved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AdminActionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    admin_id: uuid.UUID
    action_type: str
    target_type: str
    target_id: str
    reason: str
    outcome: str
    request_id: str | None = None
    created_at: datetime


class UserModerationResponse(BaseModel):
    user_id: uuid.UUID
    is_active: bool
    action_recorded_id: uuid.UUID
    updated_user: dict[str, Any]
