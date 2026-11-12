"""Repository layer for Payment Service escrow, ledger transactions, and idempotency."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    EscrowPayment,
    EscrowStatus,
    EscrowTransaction,
    PaymentEvent,
    PaymentIdempotencyRecord,
)

TERMINAL_OR_INACTIVE_ESCROW_STATUSES = {
    EscrowStatus.REFUNDED.value,
    EscrowStatus.CANCELLED.value,
}


class PaymentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_escrow_by_id(
        self,
        escrow_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> EscrowPayment | None:
        stmt = (
            select(EscrowPayment)
            .where(EscrowPayment.id == escrow_id)
            .options(
                selectinload(EscrowPayment.transactions),
                selectinload(EscrowPayment.events),
            )
        )
        if for_update:
            bind = self.session.get_bind()
            if bind is not None and bind.dialect.name != "sqlite":
                stmt = stmt.with_for_update()
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_active_escrow_for_project(self, project_id: uuid.UUID) -> EscrowPayment | None:
        stmt = (
            select(EscrowPayment)
            .where(
                EscrowPayment.project_id == project_id,
                ~EscrowPayment.status.in_(TERMINAL_OR_INACTIVE_ESCROW_STATUSES),
            )
            .options(
                selectinload(EscrowPayment.transactions),
                selectinload(EscrowPayment.events),
            )
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_escrows_for_project(self, project_id: uuid.UUID) -> list[EscrowPayment]:
        stmt = (
            select(EscrowPayment)
            .where(EscrowPayment.project_id == project_id)
            .options(
                selectinload(EscrowPayment.transactions),
                selectinload(EscrowPayment.events),
            )
            .order_by(EscrowPayment.created_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_transactions(self, escrow_id: uuid.UUID) -> list[EscrowTransaction]:
        stmt = (
            select(EscrowTransaction)
            .where(EscrowTransaction.escrow_id == escrow_id)
            .order_by(EscrowTransaction.created_at.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_idempotency_record(
        self,
        *,
        actor_id: uuid.UUID,
        operation_key: str,
        idempotency_key: str,
    ) -> PaymentIdempotencyRecord | None:
        stmt = select(PaymentIdempotencyRecord).where(
            PaymentIdempotencyRecord.actor_id == actor_id,
            PaymentIdempotencyRecord.operation_key == operation_key,
            PaymentIdempotencyRecord.idempotency_key == idempotency_key,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    def add_escrow(self, escrow: EscrowPayment) -> None:
        self.session.add(escrow)

    def add_transaction(self, txn: EscrowTransaction) -> None:
        self.session.add(txn)

    def add_event(self, event: PaymentEvent) -> None:
        self.session.add(event)

    def add_idempotency_record(self, rec: PaymentIdempotencyRecord) -> None:
        self.session.add(rec)

