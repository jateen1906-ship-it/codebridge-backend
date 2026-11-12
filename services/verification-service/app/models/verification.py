"""VerifiedWorkRecord and VerificationEvent ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class VerificationStatus(str, Enum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    CORRECTED = "CORRECTED"


class VerifiedWorkRecord(Base):
    """Cryptographically signed proof of approved freelance engineering work."""

    __tablename__ = "verified_work_records"
    __table_args__ = (
        UniqueConstraint("public_verification_id", name="uq_verified_work_public_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    public_verification_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    submission_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    freelancer_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)

    project_title: Mapped[str] = mapped_column(String(200), nullable=False)
    submission_title: Mapped[str] = mapped_column(String(200), nullable=False)
    repository_full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)

    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default=VerificationStatus.ACTIVE.value,
        index=True,
    )
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    revocation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    canonical_payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    signature_algorithm: Mapped[str] = mapped_column(String(32), nullable=False, default="HMAC-SHA256")
    signature_hex: Mapped[str] = mapped_column(String(128), nullable=False)
    key_id: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    events: Mapped[list["VerificationEvent"]] = relationship(
        back_populates="record",
        cascade="all, delete-orphan",
        order_by="VerificationEvent.created_at.asc()",
    )


class VerificationEvent(Base):
    """Immutable audit log of verification record issuance and revocation."""

    __tablename__ = "verification_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    record_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("verified_work_records.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    record: Mapped[VerifiedWorkRecord] = relationship(back_populates="events")
