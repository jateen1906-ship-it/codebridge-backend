"""Initial Admin Service schema.

Revision ID: 20261112_0001
Revises:
Create Date: 2026-11-12 15:46:00
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
        "moderation_reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("reporter_id", sa.Uuid(), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        sa.Column("resolved_by", sa.Uuid(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_moderation_reports")),
    )
    op.create_index(op.f("ix_moderation_reports_reporter_id"), "moderation_reports", ["reporter_id"])
    op.create_index(op.f("ix_moderation_reports_target_type"), "moderation_reports", ["target_type"])
    op.create_index(op.f("ix_moderation_reports_target_id"), "moderation_reports", ["target_id"])
    op.create_index(op.f("ix_moderation_reports_status"), "moderation_reports", ["status"])

    op.create_table(
        "admin_actions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("admin_id", sa.Uuid(), nullable=False),
        sa.Column("action_type", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_actions")),
    )
    op.create_index(op.f("ix_admin_actions_admin_id"), "admin_actions", ["admin_id"])
    op.create_index(op.f("ix_admin_actions_action_type"), "admin_actions", ["action_type"])
    op.create_index(op.f("ix_admin_actions_target_id"), "admin_actions", ["target_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_admin_actions_target_id"), table_name="admin_actions")
    op.drop_index(op.f("ix_admin_actions_action_type"), table_name="admin_actions")
    op.drop_index(op.f("ix_admin_actions_admin_id"), table_name="admin_actions")
    op.drop_table("admin_actions")

    op.drop_index(op.f("ix_moderation_reports_status"), table_name="moderation_reports")
    op.drop_index(op.f("ix_moderation_reports_target_id"), table_name="moderation_reports")
    op.drop_index(op.f("ix_moderation_reports_target_type"), table_name="moderation_reports")
    op.drop_index(op.f("ix_moderation_reports_reporter_id"), table_name="moderation_reports")
    op.drop_table("moderation_reports")
