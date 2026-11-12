"""Initial Observability Service schema.

Revision ID: 20261112_0001
Revises:
Create Date: 2026-11-12 00:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261112_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "observability_telemetry_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("service_name", sa.String(length=80), nullable=False),
        sa.Column("route_path", sa.String(length=255), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=False),
        sa.Column("is_error", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_obs_telemetry_service_created", "observability_telemetry_records", ["service_name", "created_at"])
    op.create_index("ix_obs_telemetry_error_created", "observability_telemetry_records", ["is_error", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_obs_telemetry_error_created", table_name="observability_telemetry_records")
    op.drop_index("ix_obs_telemetry_service_created", table_name="observability_telemetry_records")
    op.drop_table("observability_telemetry_records")
