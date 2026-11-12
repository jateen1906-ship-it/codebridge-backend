"""Async SQLAlchemy repository for hashed RefreshToken persistence and family revocation."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.refresh_token import RefreshToken


class TokenRepository:
    """Data access layer for RefreshToken records."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_token(
        self,
        *,
        user_id: uuid.UUID,
        family_id: uuid.UUID,
        jti_hash: str,
        expires_at: datetime,
        token_id: uuid.UUID | None = None,
    ) -> RefreshToken:
        record = RefreshToken(
            id=token_id or uuid.uuid4(),
            user_id=user_id,
            family_id=family_id,
            jti_hash=jti_hash,
            expires_at=expires_at,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_by_jti_hash(
        self,
        jti_hash: str,
        *,
        for_update: bool = False,
    ) -> RefreshToken | None:
        stmt = select(RefreshToken).where(RefreshToken.jti_hash == jti_hash)
        if for_update:
            bind = self.session.get_bind()
            if bind is not None and bind.dialect.name != "sqlite":
                stmt = stmt.with_for_update()
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def mark_rotated(
        self,
        *,
        record: RefreshToken,
        replaced_by_token_id: uuid.UUID,
        revoked_at: datetime,
    ) -> None:
        record.revoked_at = revoked_at
        record.replaced_by_token_id = replaced_by_token_id
        await self.session.flush()

    async def revoke_token(
        self,
        *,
        record: RefreshToken,
        revoked_at: datetime,
    ) -> None:
        if record.revoked_at is None:
            record.revoked_at = revoked_at
            await self.session.flush()

    async def revoke_family(
        self,
        *,
        family_id: uuid.UUID,
        revoked_at: datetime,
    ) -> int:
        stmt = (
            update(RefreshToken)
            .where(
                RefreshToken.family_id == family_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return int(getattr(result, "rowcount", 0) or 0)

    async def revoke_all_for_user(
        self,
        *,
        user_id: uuid.UUID,
        revoked_at: datetime,
    ) -> int:
        stmt = (
            update(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return int(getattr(result, "rowcount", 0) or 0)

