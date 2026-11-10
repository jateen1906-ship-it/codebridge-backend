"""Common OpenAPI response and standardized error schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    """Structured machine-readable error payload."""

    code: str = Field(..., examples=["INVALID_CREDENTIALS"])
    message: str = Field(..., examples=["Invalid email or password."])


class ErrorResponse(BaseModel):
    """Standardized error envelope compatible with the API Gateway."""

    success: Literal[False] = False
    error: ErrorDetail
    request_id: str = Field(..., examples=["550e8400-e29b-41d4-a716-446655440000"])


class HealthResponse(BaseModel):
    """Service health response schema."""

    status: str = Field(..., examples=["healthy"])
    service: str = Field(..., examples=["identity-service"])
    version: str = Field(..., examples=["1.0.0"])


class MessageResponse(BaseModel):
    """Generic action confirmation response."""

    success: Literal[True] = True
    message: str

