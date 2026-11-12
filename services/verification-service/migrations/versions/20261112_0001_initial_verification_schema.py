"""Initial Verification Service schema.

Revision ID: 20261112_0001
Revises:
Create Date: 2026-11-12 15:38:00
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
        "verified_work_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("public_verification_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("freelancer_id", sa.Uuid(), nullable=False),
        sa.Column("project_title", sa.String(length=200), nullable=False),
        sa.Column("submission_title", sa.String(length=200), nullable=False),
        sa.Column("repository_full_name", sa.String(length=255), nullable=True),
        sa.Column("commit_sha", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.Uuid(), nullable=True),
        sa.Column("revocation_reason", sa.Text(), nullable=True),
        sa.Column("canonical_payload_json", sa.Text(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("signature_algorithm", sa.String(length=32), nullable=False),
        sa.Column("signature_hex", sa.String(length=128), nullable=False),
        sa.Column("key_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verified_work_records")),
        sa.UniqueConstraint("public_verification_id", name="uq_verified_work_public_id"),
    )
    op.create_index(op.f("ix_verified_work_records_public_verification_id"), "verified_work_records", ["public_verification_id"])
    op.create_index(op.f("ix_verified_work_records_project_id"), "verified_work_records", ["project_id"])
    op.create_index(op.f("ix_verified_work_records_submission_id"), "verified_work_records", ["submission_id"])
    op.create_index(op.f("ix_verified_work_records_owner_id"), "verified_work_records", ["owner_id"])
    op.create_index(op.f("ix_verified_work_records_freelancer_id"), "verified_work_records", ["freelancer_id"])
    op.create_index(op.f("ix_verified_work_records_status"), "verified_work_records", ["status"])

    op.create_table(
        "verification_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("record_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["record_id"],
            ["verified_work_records.id"],
            name=op.f("fk_verification_events_record_id_verified_work_records"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verification_events")),
    )
    op.create_index(op.f("ix_verification_events_record_id"), "verification_events", ["record_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_verification_events_record_id"), table_name="verification_events")
    op.drop_table("verification_events")

    op.drop_index(op.f("ix_verified_work_records_status"), table_name="verified_work_records")
    op.drop_index(op.f("ix_verified_work_records_freelancer_id"), table_name="verified_work_records")
    op.drop_index(op.f("ix_verified_work_records_owner_id"), table_name="verified_work_records")
    op.drop_index(op.f("ix_verified_work_records_submission_id"), table_name="verified_work_records")
    op.drop_index(op.f("ix_verified_work_records_project_id"), table_name="verified_work_records")
    op.drop_index(op.f("ix_verified_work_records_public_verification_id"), table_name="verified_work_records")
    op.drop_table("verified_work_records")
