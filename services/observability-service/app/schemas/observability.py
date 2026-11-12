"""Pydantic schemas for Observability Service."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ServiceHealthSnapshot(BaseModel):
    service_name: str
    port: int
    health_status: Literal["healthy", "unhealthy", "unreachable"]
    ready_status: Literal["ready", "not_ready", "unreachable", "unsupported"]
    health_http_status: int | None = None
    ready_http_status: int | None = None
    latency_ms: float | None = None
    detail: str | None = None


class PlatformServicesStatusResponse(BaseModel):
    overall_status: Literal["healthy", "degraded", "unhealthy"]
    checked_at: datetime
    total_services: int
    healthy_count: int
    ready_count: int
    degraded_count: int
    unreachable_count: int
    services: list[ServiceHealthSnapshot]


class TelemetryCreateRequest(BaseModel):
    service_name: str = Field(min_length=2, max_length=80)
    route_path: str = Field(min_length=1, max_length=255)
    method: str = Field(min_length=2, max_length=16)
    status_code: int = Field(ge=100, le=599)
    duration_ms: float = Field(ge=0.0, le=600000.0)
    error_code: str | None = Field(default=None, max_length=80)
    error_summary: str | None = Field(default=None, max_length=1000)
    request_id: str | None = Field(default=None, max_length=120)


class TelemetryRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    service_name: str
    route_path: str
    method: str
    status_code: int
    duration_ms: float
    is_error: bool
    error_code: str | None
    error_summary: str | None
    request_id: str | None
    created_at: datetime


class ServiceMetricSummary(BaseModel):
    service_name: str
    request_count: int
    error_count: int
    error_rate_percent: float
    avg_latency_ms: float
    p95_latency_ms: float
    max_latency_ms: float


class PlatformMetricsResponse(BaseModel):
    total_requests: int
    total_errors: int
    services: list[ServiceMetricSummary]


class ErrorLogListResponse(BaseModel):
    items: list[TelemetryRecordResponse]
    total: int
