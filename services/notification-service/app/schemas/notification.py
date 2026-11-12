"""Pydantic schemas for Notification Service."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    environment: str


class ReadinessResponse(BaseModel):
    status: str
    service: str
    database: str


class NotificationEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID
    event_type: str = Field(..., min_length=2, max_length=64)
    title: str = Field(..., min_length=2, max_length=200)
    message: str = Field(..., min_length=2, max_length=4000)
    resource_type: str | None = Field(default=None, max_length=48)
    resource_id: str | None = Field(default=None, max_length=64)
    idempotency_key: str | None = Field(default=None, max_length=128)
    send_email: bool = True


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    event_type: str
    title: str
    message: str
    resource_type: str | None = None
    resource_id: str | None = None
    idempotency_key: str | None = None
    is_read: bool
    read_at: datetime | None = None
    created_at: datetime


class NotificationListResponse(BaseModel):
    total: int
    unread_count: int
    items: list[NotificationResponse]


class ReadAllResponse(BaseModel):
    marked_count: int


class NotificationPreferencesResponse(BaseModel):
    user_id: uuid.UUID
    in_app_enabled: bool
    email_enabled: bool
    muted_event_types: list[str]
    updated_at: datetime | None = None


class NotificationPreferencesUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    in_app_enabled: bool = True
    email_enabled: bool = True
    muted_event_types: list[str] = Field(default_factory=list, max_length=50)
