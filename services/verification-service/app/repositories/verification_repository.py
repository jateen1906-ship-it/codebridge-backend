"""Repository persistence layer for Verification Service."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import VerificationEvent, VerificationStatus, VerifiedWorkRecord


class VerificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, record_id: uuid.UUID) -> VerifiedWorkRecord | None:
        stmt = (
            select(VerifiedWorkRecord)
            .where(VerifiedWorkRecord.id == record_id)
            .options(selectinload(VerifiedWorkRecord.events))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_public_id(self, public_verification_id: str) -> VerifiedWorkRecord | None:
        stmt = (
            select(VerifiedWorkRecord)
            .where(VerifiedWorkRecord.public_verification_id == public_verification_id)
            .options(selectinload(VerifiedWorkRecord.events))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_active_by_submission(self, submission_id: uuid.UUID) -> VerifiedWorkRecord | None:
        stmt = (
            select(VerifiedWorkRecord)
            .where(
                VerifiedWorkRecord.submission_id == submission_id,
                VerifiedWorkRecord.status == VerificationStatus.ACTIVE.value,
            )
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_by_project(self, project_id: uuid.UUID) -> list[VerifiedWorkRecord]:
        stmt = (
            select(VerifiedWorkRecord)
            .where(VerifiedWorkRecord.project_id == project_id)
            .options(selectinload(VerifiedWorkRecord.events))
            .order_by(VerifiedWorkRecord.issued_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    def add_record(self, record: VerifiedWorkRecord) -> None:
        self.session.add(record)

    def add_event(self, event: VerificationEvent) -> None:
        self.session.add(event)
