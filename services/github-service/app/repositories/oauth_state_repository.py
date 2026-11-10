"""Repository helpers for single-use OAuth state persistence and atomic consumption."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.oauth_state import OAuthState


class OAuthStateRepository:
    """Data-access layer for hashed OAuth CSRF state records."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        state_hash: str,
        owner_id: uuid.UUID,
        project_id: uuid.UUID,
        expires_at: datetime,
    ) -> OAuthState:
        """Persist a hashed single-use OAuth state bound to owner_id and project_id."""
        record = OAuthState(
            state_hash=state_hash,
            owner_id=owner_id,
            project_id=project_id,
            expires_at=expires_at,
        )
        self._session.add(record)
        await self._session.flush()
        return record

    async def get_by_state_hash(self, state_hash: str) -> OAuthState | None:
        """Look up an OAuth state record by SHA-256 hash."""
        stmt = select(OAuthState).where(OAuthState.state_hash == state_hash)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def consume_atomically(self, *, state_id: uuid.UUID, now: datetime) -> bool:
        """Atomically mark a state record consumed only if unused and unexpired."""
        stmt = (
            update(OAuthState)
            .where(
                OAuthState.id == state_id,
                OAuthState.consumed_at.is_(None),
                OAuthState.expires_at > now,
            )
            .values(consumed_at=now)
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(stmt)
        return (result.rowcount or 0) == 1
