"""Async repository for persisted RepositoryInsight reports."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.repository_insight import RepositoryInsight


class RepositoryInsightRepository:
    """Data-access layer for deterministic + AI repository insights."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, insight: RepositoryInsight) -> RepositoryInsight:
        self._session.add(insight)
        await self._session.flush()
        await self._session.refresh(insight)
        return insight

    async def get_latest_for_project(self, project_id: uuid.UUID) -> RepositoryInsight | None:
        stmt = (
            select(RepositoryInsight)
            .where(RepositoryInsight.project_id == project_id)
            .order_by(RepositoryInsight.generated_at.desc(), RepositoryInsight.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_referenced_index_ids(self, project_id: uuid.UUID) -> set[uuid.UUID]:
        stmt = select(RepositoryInsight.repository_index_id).where(
            RepositoryInsight.project_id == project_id
        )
        result = await self._session.execute(stmt)
        return {row[0] for row in result.all()}

