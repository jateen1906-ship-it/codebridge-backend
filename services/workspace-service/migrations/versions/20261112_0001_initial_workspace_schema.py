"""Initial workspace schema

Revision ID: 20261112_0001
Revises:
Create Date: 2026-11-12 15:30:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20261112_0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_freelancer_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("repository_full_name", sa.String(length=255), nullable=False),
        sa.Column("repository_ref", sa.String(length=200), nullable=False),
        sa.Column("snapshot_sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "PROVISIONING",
                "READY",
                "RUNNING",
                "STOPPED",
                "FAILED",
                "EXPIRED",
                "DESTROYED",
                name="workspace_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("container_id", sa.String(length=128), nullable=True),
        sa.Column("container_name", sa.String(length=128), nullable=False),
        sa.Column("workspace_path", sa.String(length=500), nullable=False),
        sa.Column("runtime_image", sa.String(length=128), nullable=False),
        sa.Column("cpu_limit", sa.String(length=16), nullable=False),
        sa.Column("memory_limit_mb", sa.Integer(), nullable=False),
        sa.Column("pids_limit", sa.Integer(), nullable=False),
        sa.Column("network_disabled", sa.Boolean(), nullable=False),
        sa.Column("storage_bytes", sa.Integer(), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("destroyed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("memory_limit_mb >= 64", name="ck_workspaces_memory_min"),
        sa.CheckConstraint("pids_limit >= 16", name="ck_workspaces_pids_min"),
        sa.CheckConstraint("storage_bytes >= 0", name="ck_workspaces_storage_non_negative"),
        sa.CheckConstraint("file_count >= 0", name="ck_workspaces_file_count_non_negative"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("container_name"),
    )
    op.create_index(op.f("ix_workspaces_assigned_freelancer_id"), "workspaces", ["assigned_freelancer_id"], unique=False)
    op.create_index(op.f("ix_workspaces_created_at"), "workspaces", ["created_at"], unique=False)
    op.create_index(op.f("ix_workspaces_created_by"), "workspaces", ["created_by"], unique=False)
    op.create_index(op.f("ix_workspaces_expires_at"), "workspaces", ["expires_at"], unique=False)
    op.create_index(op.f("ix_workspaces_owner_id"), "workspaces", ["owner_id"], unique=False)
    op.create_index(op.f("ix_workspaces_project_id"), "workspaces", ["project_id"], unique=False)
    op.create_index(op.f("ix_workspaces_status"), "workspaces", ["status"], unique=False)
    op.create_index(
        "uq_workspaces_one_active_per_project",
        "workspaces",
        ["project_id"],
        unique=True,
        sqlite_where=sa.text("status NOT IN ('FAILED', 'EXPIRED', 'DESTROYED')"),
        postgresql_where=sa.text("status NOT IN ('FAILED', 'EXPIRED', 'DESTROYED')"),
    )

    op.create_table(
        "workspace_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("previous_status", sa.String(length=32), nullable=True),
        sa.Column("new_status", sa.String(length=32), nullable=False),
        sa.Column(
            "details",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_workspace_events_actor_id"), "workspace_events", ["actor_id"], unique=False)
    op.create_index(op.f("ix_workspace_events_created_at"), "workspace_events", ["created_at"], unique=False)
    op.create_index(op.f("ix_workspace_events_workspace_id"), "workspace_events", ["workspace_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_workspace_events_workspace_id"), table_name="workspace_events")
    op.drop_index(op.f("ix_workspace_events_created_at"), table_name="workspace_events")
    op.drop_index(op.f("ix_workspace_events_actor_id"), table_name="workspace_events")
    op.drop_table("workspace_events")

    op.drop_index("uq_workspaces_one_active_per_project", table_name="workspaces")
    op.drop_index(op.f("ix_workspaces_status"), table_name="workspaces")
    op.drop_index(op.f("ix_workspaces_project_id"), table_name="workspaces")
    op.drop_index(op.f("ix_workspaces_owner_id"), table_name="workspaces")
    op.drop_index(op.f("ix_workspaces_expires_at"), table_name="workspaces")
    op.drop_index(op.f("ix_workspaces_created_by"), table_name="workspaces")
    op.drop_index(op.f("ix_workspaces_created_at"), table_name="workspaces")
    op.drop_index(op.f("ix_workspaces_assigned_freelancer_id"), table_name="workspaces")
    op.drop_table("workspaces")

