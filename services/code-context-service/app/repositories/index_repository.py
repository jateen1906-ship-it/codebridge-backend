"""Async repository for versioned RepositoryIndex records."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.repository_index import IndexStatus, RepositoryIndex


class RepositoryIndexRepository:
    """Data-access layer for project-scoped repository indexes."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, index_id: uuid.UUID) -> RepositoryIndex | None:
        stmt = select(RepositoryIndex).where(RepositoryIndex.id == index_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_active_for_project(self, project_id: uuid.UUID) -> RepositoryIndex | None:
        stmt = (
            select(RepositoryIndex)
            .where(
                RepositoryIndex.project_id == project_id,
                RepositoryIndex.is_active.is_(True),
            )
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_latest_for_project(self, project_id: uuid.UUID) -> RepositoryIndex | None:
        stmt = (
            select(RepositoryIndex)
            .where(RepositoryIndex.project_id == project_id)
            .order_by(RepositoryIndex.index_version.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_next_version(self, project_id: uuid.UUID) -> int:
        stmt = select(func.coalesce(func.max(RepositoryIndex.index_version), 0)).where(
            RepositoryIndex.project_id == project_id
        )
        result = await self._session.execute(stmt)
        current_max = int(result.scalar_one())
        return current_max + 1

    async def list_for_project(self, project_id: uuid.UUID) -> list[RepositoryIndex]:
        stmt = (
            select(RepositoryIndex)
            .where(RepositoryIndex.project_id == project_id)
            .order_by(RepositoryIndex.index_version.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create(self, index: RepositoryIndex) -> RepositoryIndex:
        self._session.add(index)
        await self._session.flush()
        await self._session.refresh(index)
        return index

    async def activate_version(
        self,
        *,
        project_id: uuid.UUID,
        index_id: uuid.UUID,
        commit_sha: str | None,
        files_discovered: int,
        files_indexed: int,
        files_skipped: int,
        chunks_generated: int,
        total_bytes_indexed: int,
        completed_at: datetime,
    ) -> RepositoryIndex | None:
        """Atomically retire any existing active index and promote the validated version."""
        deactivate_stmt = (
            update(RepositoryIndex)
            .where(
                RepositoryIndex.project_id == project_id,
                RepositoryIndex.is_active.is_(True),
                RepositoryIndex.id != index_id,
            )
            .values(
                is_active=False,
                status=IndexStatus.STALE,
                updated_at=completed_at,
            )
            .execution_options(synchronize_session=False)
        )
        await self._session.execute(deactivate_stmt)
        await self._session.flush()

        activate_stmt = (
            update(RepositoryIndex)
            .where(RepositoryIndex.id == index_id)
            .values(
                commit_sha=commit_sha,
                status=IndexStatus.READY,
                is_active=True,
                files_discovered=files_discovered,
                files_indexed=files_indexed,
                files_skipped=files_skipped,
                chunks_generated=chunks_generated,
                total_bytes_indexed=total_bytes_indexed,
                completed_at=completed_at,
                error_message=None,
                updated_at=completed_at,
            )
            .execution_options(synchronize_session=False)
        )
        await self._session.execute(activate_stmt)
        await self._session.flush()
        row = await self.get_by_id(index_id)
        if row is not None:
            await self._session.refresh(row)
        return row

    async def mark_failed(
        self,
        *,
        index_id: uuid.UUID,
        error_message: str,
        completed_at: datetime,
        files_discovered: int = 0,
        files_indexed: int = 0,
        files_skipped: int = 0,
    ) -> None:
        stmt = (
            update(RepositoryIndex)
            .where(RepositoryIndex.id == index_id)
            .values(
                status=IndexStatus.FAILED,
                is_active=False,
                files_discovered=files_discovered,
                files_indexed=files_indexed,
                files_skipped=files_skipped,
                error_message=error_message[:2000],
                completed_at=completed_at,
                updated_at=completed_at,
            )
            .execution_options(synchronize_session=False)
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def mark_stale_by_id(self, *, index_id: uuid.UUID, updated_at: datetime) -> None:
        stmt = (
            update(RepositoryIndex)
            .where(RepositoryIndex.id == index_id)
            .values(
                status=IndexStatus.STALE,
                is_active=False,
                updated_at=updated_at,
            )
            .execution_options(synchronize_session=False)
        )
        await self._session.execute(stmt)
        await self._session.flush()
