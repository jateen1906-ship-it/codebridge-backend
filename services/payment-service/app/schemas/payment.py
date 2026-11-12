"""Pydantic schemas for Payment Service simulated escrow lifecycle."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class CreateEscrowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Decimal | None = Field(default=None, gt=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    fund_immediately: bool = False
    hold_immediately: bool = False
    notes: str | None = Field(default=None, max_length=500)


class FundEscrowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payment_method_reference: str = Field(default="sim_card_visa_4242", min_length=3, max_length=128)
    hold_if_assigned: bool = True
    notes: str | None = Field(default=None, max_length=500)


class EscrowActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=500)


class RefundEscrowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=5, max_length=1000)
    process_immediately: bool = True


class EscrowTransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    escrow_id: uuid.UUID
    project_id: uuid.UUID
    actor_id: uuid.UUID
    transaction_type: str
    amount: Decimal
    currency: str
    idempotency_key: str | None = None
    reference_id: str | None = None
    notes: str | None = None
    created_at: datetime


class PaymentEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    actor_id: uuid.UUID
    from_status: str | None = None
    to_status: str
    detail: str | None = None
    request_id: str | None = None
    created_at: datetime


class EscrowPaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    owner_id: uuid.UUID
    freelancer_id: uuid.UUID | None = None
    amount: Decimal
    currency: str
    status: str
    funding_reference: str | None = None
    release_reference: str | None = None
    refund_reference: str | None = None
    funded_at: datetime | None = None
    held_at: datetime | None = None
    released_at: datetime | None = None
    refunded_at: datetime | None = None
    cancelled_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    transactions: list[EscrowTransactionResponse] = Field(default_factory=list)
    events: list[PaymentEventResponse] = Field(default_factory=list)


class EscrowListResponse(BaseModel):
    project_id: uuid.UUID
    total: int
    items: list[EscrowPaymentResponse]
