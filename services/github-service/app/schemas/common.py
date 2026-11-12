"""Common health, readiness, and standardized error schemas for GitHub Service."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    """Structured machine-readable error payload."""

    code: str = Field(..., examples=["GITHUB_CONNECTION_NOT_FOUND"])
    message: str = Field(..., examples=["No active GitHub OAuth connection was found."])


class ErrorResponse(BaseModel):
    """Standardized error envelope compatible with API Gateway."""

    success: Literal[False] = False
    error: ErrorDetail
    request_id: str = Field(..., examples=["550e8400-e29b-41d4-a716-446655440000"])


class HealthResponse(BaseModel):
    """GitHub Service liveness response schema."""

    status: str = Field(..., examples=["healthy"])
    service: str = Field(..., examples=["github-service"])
    version: str = Field(..., examples=["1.0.0"])


class ReadinessResponse(BaseModel):
    """GitHub Service readiness response schema without sensitive diagnostics."""

    status: Literal["ready", "not_ready"]
    service: str = Field(..., examples=["github-service"])
    version: str = Field(..., examples=["1.0.0"])
    database: Literal["connected", "unavailable"]

