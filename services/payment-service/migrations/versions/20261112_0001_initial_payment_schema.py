"""Initial Payment Service schema.

Revision ID: 20261112_0001
Revises:
Create Date: 2026-11-12 15:40:00
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261112_0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "escrow_payments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("freelancer_id", sa.Uuid(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("funding_reference", sa.String(length=128), nullable=True),
        sa.Column("release_reference", sa.String(length=128), nullable=True),
        sa.Column("refund_reference", sa.String(length=128), nullable=True),
        sa.Column("funded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("held_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_escrow_payments")),
    )
    op.create_index(op.f("ix_escrow_payments_project_id"), "escrow_payments", ["project_id"])
    op.create_index(op.f("ix_escrow_payments_owner_id"), "escrow_payments", ["owner_id"])
    op.create_index(op.f("ix_escrow_payments_freelancer_id"), "escrow_payments", ["freelancer_id"])
    op.create_index(op.f("ix_escrow_payments_status"), "escrow_payments", ["status"])

    op.create_table(
        "escrow_transactions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("escrow_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("transaction_type", sa.String(length=32), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("reference_id", sa.String(length=128), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["escrow_id"],
            ["escrow_payments.id"],
            name=op.f("fk_escrow_transactions_escrow_id_escrow_payments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_escrow_transactions")),
    )
    op.create_index(op.f("ix_escrow_transactions_escrow_id"), "escrow_transactions", ["escrow_id"])
    op.create_index(op.f("ix_escrow_transactions_project_id"), "escrow_transactions", ["project_id"])

    op.create_table(
        "payment_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("escrow_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["escrow_id"],
            ["escrow_payments.id"],
            name=op.f("fk_payment_events_escrow_id_escrow_payments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_events")),
    )
    op.create_index(op.f("ix_payment_events_escrow_id"), "payment_events", ["escrow_id"])

    op.create_table(
        "payment_idempotency_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("operation_key", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("response_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_idempotency_records")),
        sa.UniqueConstraint(
            "actor_id",
            "operation_key",
            "idempotency_key",
            name="uq_payment_idempotency_actor_op_key",
        ),
    )


def downgrade() -> None:
    op.drop_table("payment_idempotency_records")

    op.drop_index(op.f("ix_payment_events_escrow_id"), table_name="payment_events")
    op.drop_table("payment_events")

    op.drop_index(op.f("ix_escrow_transactions_project_id"), table_name="escrow_transactions")
    op.drop_index(op.f("ix_escrow_transactions_escrow_id"), table_name="escrow_transactions")
    op.drop_table("escrow_transactions")

    op.drop_index(op.f("ix_escrow_payments_status"), table_name="escrow_payments")
    op.drop_index(op.f("ix_escrow_payments_freelancer_id"), table_name="escrow_payments")
    op.drop_index(op.f("ix_escrow_payments_owner_id"), table_name="escrow_payments")
    op.drop_index(op.f("ix_escrow_payments_project_id"), table_name="escrow_payments")
    op.drop_table("escrow_payments")
