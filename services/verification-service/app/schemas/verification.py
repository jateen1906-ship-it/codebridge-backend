"""Pydantic schemas for Verification Service issuance, lookup, cryptographic check, and revocation."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CreateVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: uuid.UUID
    submission_id: uuid.UUID


class RevokeVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["REVOKED", "CORRECTED"] = "REVOKED"
    reason: str = Field(..., min_length=5, max_length=2000)

    @field_validator("reason")
    @classmethod
    def _clean_reason(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 5:
            raise ValueError("reason must be at least 5 characters.")
        return cleaned


class VerificationEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    actor_id: uuid.UUID
    event_type: str
    detail: str | None = None
    request_id: str | None = None
    created_at: datetime


class VerifiedWorkRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    public_verification_id: str
    project_id: uuid.UUID
    submission_id: uuid.UUID
    owner_id: uuid.UUID
    freelancer_id: uuid.UUID
    project_title: str
    submission_title: str
    repository_full_name: str | None = None
    commit_sha: str | None = None
    status: str
    approved_at: datetime
    issued_at: datetime
    revoked_at: datetime | None = None
    revocation_reason: str | None = None
    payload_sha256: str
    signature_algorithm: str
    signature_hex: str
    key_id: str
    signature_valid: bool = True
    is_valid: bool = True
    events: list[VerificationEventResponse] = Field(default_factory=list)


class PublicVerificationResponse(BaseModel):
    """Sanitized public proof returned without requiring authentication (zero PII/OAuth)."""

    public_verification_id: str
    status: str
    is_valid: bool
    signature_valid: bool
    project_id: uuid.UUID
    project_title: str
    submission_title: str
    freelancer_id: uuid.UUID
    repository_full_name: str | None = None
    commit_sha: str | None = None
    approved_at: datetime
    issued_at: datetime
    revoked_at: datetime | None = None
    revocation_reason: str | None = None
    payload_sha256: str
    signature_algorithm: str
    key_id: str


class VerificationCheckResponse(BaseModel):
    record_id: uuid.UUID
    public_verification_id: str
    status: str
    payload_hash_matches: bool
    signature_valid: bool
    is_valid: bool
    checked_at: datetime


class ProjectVerificationListResponse(BaseModel):
    project_id: uuid.UUID
    total: int
    items: list[VerifiedWorkRecordResponse]


class FreelancerVerificationListResponse(BaseModel):
    freelancer_id: uuid.UUID
    total: int
    items: list[PublicVerificationResponse]


class MyVerifiedWorkListResponse(BaseModel):
    user_id: uuid.UUID
    total: int
    items: list[VerifiedWorkRecordResponse]

