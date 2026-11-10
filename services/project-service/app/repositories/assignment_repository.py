"""Async SQLAlchemy repository for Assignment persistence and active lookup."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assignment import Assignment, AssignmentStatus


class AssignmentRepository:
    """Data access layer for Assignment entities."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_active_by_project_id(
        self,
        project_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> Assignment | None:
        stmt = select(Assignment).where(
            Assignment.project_id == project_id,
            Assignment.status == AssignmentStatus.ACTIVE,
        )
        if for_update:
            bind = self.session.get_bind()
            if bind is not None and bind.dialect.name != "sqlite":
                stmt = stmt.with_for_update()
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_project_id(self, project_id: uuid.UUID) -> list[Assignment]:
        stmt = (
            select(Assignment)
            .where(Assignment.project_id == project_id)
            .order_by(Assignment.assigned_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def create_assignment(
        self,
        *,
        project_id: uuid.UUID,
        freelancer_id: uuid.UUID,
        assigned_by: uuid.UUID,
        assigned_at: datetime,
    ) -> Assignment:
        assignment = Assignment(
            project_id=project_id,
            freelancer_id=freelancer_id,
            assigned_by=assigned_by,
            status=AssignmentStatus.ACTIVE,
            assigned_at=assigned_at,
            updated_at=assigned_at,
        )
        self.session.add(assignment)
        await self.session.flush()
        return assignment

