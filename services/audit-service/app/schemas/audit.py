"""Pydantic schemas for Audit Service."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

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


class AuditEventCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_name: str = Field(..., min_length=2, max_length=64)
    actor_id: uuid.UUID | None = None
    actor_role: str | None = Field(default=None, max_length=24)
    action: str = Field(..., min_length=2, max_length=100)
    resource_type: str = Field(..., min_length=2, max_length=64)
    resource_id: str | None = Field(default=None, max_length=128)
    project_id: uuid.UUID | None = None
    outcome: Literal["SUCCESS", "DENIED", "ERROR", "WARNING"] = "SUCCESS"
    ip_address: str | None = Field(default=None, max_length=64)
    request_id: str | None = Field(default=None, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuditLogResponse(BaseModel):
    id: uuid.UUID
    service_name: str
    actor_id: uuid.UUID | None = None
    actor_role: str | None = None
    action: str
    resource_type: str
    resource_id: str | None = None
    project_id: uuid.UUID | None = None
    outcome: str
    ip_address: str | None = None
    request_id: str | None = None
    metadata: dict[str, Any]
    created_at: datetime


class AuditLogListResponse(BaseModel):
    total: int
    items: list[AuditLogResponse]
