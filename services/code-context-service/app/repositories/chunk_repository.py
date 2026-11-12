"""Async repository for compact CodeChunk metadata records."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.code_chunk import CodeChunk


class CodeChunkRepository:
    """Data-access layer for CodeChunk line-range & vector linkage metadata."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def bulk_create(self, chunks: list[CodeChunk]) -> list[CodeChunk]:
        if not chunks:
            return []
        self._session.add_all(chunks)
        await self._session.flush()
        return chunks

    async def count_by_index_id(self, repository_index_id: uuid.UUID) -> int:
        stmt = select(func.count(CodeChunk.id)).where(
            CodeChunk.repository_index_id == repository_index_id
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def list_by_index_id(self, repository_index_id: uuid.UUID) -> list[CodeChunk]:
        stmt = (
            select(CodeChunk)
            .where(CodeChunk.repository_index_id == repository_index_id)
            .order_by(CodeChunk.file_path.asc(), CodeChunk.chunk_index.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

