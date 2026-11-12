"""Escrow payment, transaction ledger, idempotency, and event ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EscrowStatus(str, Enum):
    CREATED = "CREATED"
    FUNDED = "FUNDED"
    HELD = "HELD"
    RELEASE_PENDING = "RELEASE_PENDING"
    RELEASED = "RELEASED"
    REFUND_REQUESTED = "REFUND_REQUESTED"
    REFUNDED = "REFUNDED"
    CANCELLED = "CANCELLED"


class TransactionType(str, Enum):
    CREATE = "CREATE"
    FUND = "FUND"
    HOLD = "HOLD"
    RELEASE = "RELEASE"
    REFUND_REQUEST = "REFUND_REQUEST"
    REFUND = "REFUND"
    CANCEL = "CANCEL"


class EscrowPayment(Base):
    """Simulated escrow record owned by Payment Service."""

    __tablename__ = "escrow_payments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    freelancer_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=EscrowStatus.CREATED.value,
        index=True,
    )
    funding_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    release_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    refund_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    funded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    held_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    transactions: Mapped[list["EscrowTransaction"]] = relationship(
        back_populates="escrow",
        cascade="all, delete-orphan",
        order_by="EscrowTransaction.created_at.asc()",
    )
    events: Mapped[list["PaymentEvent"]] = relationship(
        back_populates="escrow",
        cascade="all, delete-orphan",
        order_by="PaymentEvent.created_at.asc()",
    )


class EscrowTransaction(Base):
    """Immutable financial ledger row for an escrow operation."""

    __tablename__ = "escrow_transactions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    escrow_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("escrow_payments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    actor_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    transaction_type: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reference_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    escrow: Mapped[EscrowPayment] = relationship(back_populates="transactions")


class PaymentEvent(Base):
    """Chronological state transition history for escrow payments."""

    __tablename__ = "payment_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    escrow_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("escrow_payments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    escrow: Mapped[EscrowPayment] = relationship(back_populates="events")


class PaymentIdempotencyRecord(Base):
    """Persisted idempotency key store guarding financial retries and preventing payload reuse."""

    __tablename__ = "payment_idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "actor_id",
            "operation_key",
            "idempotency_key",
            name="uq_payment_idempotency_actor_op_key",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    operation_key: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    response_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
