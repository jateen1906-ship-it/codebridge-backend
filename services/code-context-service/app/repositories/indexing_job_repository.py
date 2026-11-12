"""Async repository for IndexingJob execution and concurrency-guard records."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.indexing_job import IndexingJob, IndexingJobStatus


class IndexingJobRepository:
    """Data-access layer for repository indexing job runs."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_inflight_for_project(self, project_id: uuid.UUID) -> IndexingJob | None:
        stmt = (
            select(IndexingJob)
            .where(
                IndexingJob.project_id == project_id,
                IndexingJob.status.in_([IndexingJobStatus.PENDING, IndexingJobStatus.RUNNING]),
            )
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_latest_for_project(self, project_id: uuid.UUID) -> IndexingJob | None:
        stmt = (
            select(IndexingJob)
            .where(IndexingJob.project_id == project_id)
            .order_by(IndexingJob.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, job_id: uuid.UUID) -> IndexingJob | None:
        stmt = select(IndexingJob).where(IndexingJob.id == job_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(self, job: IndexingJob) -> IndexingJob:
        self._session.add(job)
        await self._session.flush()
        await self._session.refresh(job)
        return job

    async def mark_completed(
        self,
        *,
        job_id: uuid.UUID,
        completed_at: datetime,
        files_processed: int,
        files_skipped: int,
        chunks_generated: int,
    ) -> IndexingJob | None:
        stmt = (
            update(IndexingJob)
            .where(IndexingJob.id == job_id)
            .values(
                status=IndexingJobStatus.COMPLETED,
                completed_at=completed_at,
                files_processed=files_processed,
                files_skipped=files_skipped,
                chunks_generated=chunks_generated,
                error_code=None,
                error_message=None,
                updated_at=completed_at,
            )
            .execution_options(synchronize_session=False)
        )
        await self._session.execute(stmt)
        await self._session.flush()
        row = await self.get_by_id(job_id)
        if row is not None:
            await self._session.refresh(row)
        return row

    async def mark_failed(
        self,
        *,
        job_id: uuid.UUID,
        completed_at: datetime,
        error_code: str,
        error_message: str,
        files_processed: int = 0,
        files_skipped: int = 0,
    ) -> None:
        stmt = (
            update(IndexingJob)
            .where(IndexingJob.id == job_id)
            .values(
                status=IndexingJobStatus.FAILED,
                completed_at=completed_at,
                files_processed=files_processed,
                files_skipped=files_skipped,
                error_code=error_code[:64],
                error_message=error_message[:2000],
                updated_at=completed_at,
            )
            .execution_options(synchronize_session=False)
        )
        await self._session.execute(stmt)
        await self._session.flush()
