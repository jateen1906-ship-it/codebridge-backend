"""Initial Review Service schema.

Revision ID: 20261112_0001
Revises:
Create Date: 2026-11-12 15:35:00
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
        "submissions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("freelancer_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("changes_summary", sa.Text(), nullable=False),
        sa.Column("test_results_summary", sa.Text(), nullable=True),
        sa.Column("git_ref", sa.String(length=255), nullable=True),
        sa.Column("commit_sha", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_submissions")),
        sa.UniqueConstraint("project_id", "version_number", name="uq_submissions_project_version"),
    )
    op.create_index(op.f("ix_submissions_project_id"), "submissions", ["project_id"])
    op.create_index(op.f("ix_submissions_freelancer_id"), "submissions", ["freelancer_id"])
    op.create_index(op.f("ix_submissions_owner_id"), "submissions", ["owner_id"])
    op.create_index(op.f("ix_submissions_workspace_id"), "submissions", ["workspace_id"])
    op.create_index(op.f("ix_submissions_status"), "submissions", ["status"])

    op.create_table(
        "submission_files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("file_path", sa.String(length=500), nullable=False),
        sa.Column("change_type", sa.String(length=16), nullable=False),
        sa.Column("summary", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["submissions.id"],
            name=op.f("fk_submission_files_submission_id_submissions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_submission_files")),
    )
    op.create_index(op.f("ix_submission_files_submission_id"), "submission_files", ["submission_id"])

    op.create_table(
        "reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("reviewer_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("feedback", sa.Text(), nullable=False),
        sa.Column("checklist_passed", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["submissions.id"],
            name=op.f("fk_reviews_submission_id_submissions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reviews")),
    )
    op.create_index(op.f("ix_reviews_submission_id"), "reviews", ["submission_id"])
    op.create_index(op.f("ix_reviews_project_id"), "reviews", ["project_id"])
    op.create_index(op.f("ix_reviews_reviewer_id"), "reviews", ["reviewer_id"])
    op.create_index(op.f("ix_reviews_decision"), "reviews", ["decision"])

    op.create_table(
        "review_comments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=False),
        sa.Column("author_role", sa.String(length=24), nullable=False),
        sa.Column("file_path", sa.String(length=500), nullable=True),
        sa.Column("line_number", sa.Integer(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["submissions.id"],
            name=op.f("fk_review_comments_submission_id_submissions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_comments")),
    )
    op.create_index(op.f("ix_review_comments_submission_id"), "review_comments", ["submission_id"])
    op.create_index(op.f("ix_review_comments_author_id"), "review_comments", ["author_id"])

    op.create_table(
        "review_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["submissions.id"],
            name=op.f("fk_review_events_submission_id_submissions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_events")),
    )
    op.create_index(op.f("ix_review_events_submission_id"), "review_events", ["submission_id"])
    op.create_index(op.f("ix_review_events_project_id"), "review_events", ["project_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_review_events_project_id"), table_name="review_events")
    op.drop_index(op.f("ix_review_events_submission_id"), table_name="review_events")
    op.drop_table("review_events")

    op.drop_index(op.f("ix_review_comments_author_id"), table_name="review_comments")
    op.drop_index(op.f("ix_review_comments_submission_id"), table_name="review_comments")
    op.drop_table("review_comments")

    op.drop_index(op.f("ix_reviews_decision"), table_name="reviews")
    op.drop_index(op.f("ix_reviews_reviewer_id"), table_name="reviews")
    op.drop_index(op.f("ix_reviews_project_id"), table_name="reviews")
    op.drop_index(op.f("ix_reviews_submission_id"), table_name="reviews")
    op.drop_table("reviews")

    op.drop_index(op.f("ix_submission_files_submission_id"), table_name="submission_files")
    op.drop_table("submission_files")

    op.drop_index(op.f("ix_submissions_status"), table_name="submissions")
    op.drop_index(op.f("ix_submissions_workspace_id"), table_name="submissions")
    op.drop_index(op.f("ix_submissions_owner_id"), table_name="submissions")
    op.drop_index(op.f("ix_submissions_freelancer_id"), table_name="submissions")
    op.drop_index(op.f("ix_submissions_project_id"), table_name="submissions")
    op.drop_table("submissions")

