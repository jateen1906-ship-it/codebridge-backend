"""Common health, readiness, and error response schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "code-context-service"
    version: str = "1.0.0"


class ReadinessResponse(BaseModel):
    status: Literal["ready", "degraded"]
    service: str = "code-context-service"
    database: Literal["ok", "error"]
    vector_store: Literal["ok", "error"]
    identity_client: Literal["ok", "error"]
    project_client: Literal["ok", "error"]
    github_client: Literal["ok", "error"]
    metrics: dict[str, float] = Field(default_factory=dict)


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    success: Literal[False] = False
    error: ErrorDetail
    request_id: str

