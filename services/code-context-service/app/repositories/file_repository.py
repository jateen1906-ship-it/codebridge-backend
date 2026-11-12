"""Async repository for IndexedFile inventory and skip records."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.indexed_file import IndexedFile


class IndexedFileRepository:
    """Data-access layer for per-file indexing records."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def bulk_create(self, files: list[IndexedFile]) -> list[IndexedFile]:
        if not files:
            return []
        self._session.add_all(files)
        await self._session.flush()
        return files

    async def list_by_index_id(self, repository_index_id: uuid.UUID) -> list[IndexedFile]:
        stmt = (
            select(IndexedFile)
            .where(IndexedFile.repository_index_id == repository_index_id)
            .order_by(IndexedFile.file_path.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

