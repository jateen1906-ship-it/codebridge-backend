"""Common schemas and standardized error models for Review Service."""

from __future__ import annotations

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    success: bool = False
    error: ErrorDetail
    request_id: str


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    environment: str


class ReadinessResponse(BaseModel):
    status: str
    service: str
    database: str

