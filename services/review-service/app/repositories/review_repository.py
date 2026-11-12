"""Repository layer for Review Service ORM persistence and queries."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Review, ReviewComment, ReviewEvent, Submission, SubmissionStatus

OPEN_SUBMISSION_STATUSES = (
    SubmissionStatus.DRAFT.value,
    SubmissionStatus.SUBMITTED.value,
    SubmissionStatus.UNDER_REVIEW.value,
)


class ReviewRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_submission_by_id(self, submission_id: uuid.UUID) -> Submission | None:
        stmt = (
            select(Submission)
            .where(Submission.id == submission_id)
            .options(
                selectinload(Submission.files),
                selectinload(Submission.reviews),
                selectinload(Submission.comments),
                selectinload(Submission.events),
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_open_submission_for_project(self, project_id: uuid.UUID) -> Submission | None:
        stmt = (
            select(Submission)
            .where(
                Submission.project_id == project_id,
                Submission.status.in_(OPEN_SUBMISSION_STATUSES),
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_next_version_number(self, project_id: uuid.UUID) -> int:
        stmt = select(func.coalesce(func.max(Submission.version_number), 0)).where(
            Submission.project_id == project_id
        )
        current_max = await self.session.scalar(stmt)
        return int(current_max or 0) + 1

    async def list_submissions_for_project(self, project_id: uuid.UUID) -> list[Submission]:
        stmt = (
            select(Submission)
            .where(Submission.project_id == project_id)
            .options(
                selectinload(Submission.files),
                selectinload(Submission.reviews),
                selectinload(Submission.comments),
                selectinload(Submission.events),
            )
            .order_by(Submission.version_number.desc(), Submission.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_latest_review_for_submission(self, submission_id: uuid.UUID) -> Review | None:
        stmt = (
            select(Review)
            .where(Review.submission_id == submission_id)
            .order_by(Review.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    def add_submission(self, submission: Submission) -> None:
        self.session.add(submission)

    def add_review(self, review: Review) -> None:
        self.session.add(review)

    def add_comment(self, comment: ReviewComment) -> None:
        self.session.add(comment)

    def add_event(self, event: ReviewEvent) -> None:
        self.session.add(event)

