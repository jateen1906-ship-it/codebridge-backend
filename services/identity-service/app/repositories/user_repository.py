"""Async SQLAlchemy repository for User persistence."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.user import User, UserRole


class UserRepository:
    """Data access layer for User entities."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(
        self,
        user_id: uuid.UUID,
        *,
        include_profile: bool = False,
    ) -> User | None:
        stmt = select(User).where(User.id == user_id)
        if include_profile:
            stmt = stmt.options(selectinload(User.freelancer_profile))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        normalized = email.strip().lower()
        stmt = select(User).where(User.email == normalized)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_user(
        self,
        *,
        full_name: str,
        email: str,
        password_hash: str,
        role: UserRole,
        is_active: bool = True,
        is_verified: bool = False,
    ) -> User:
        user = User(
            full_name=full_name,
            email=email.strip().lower(),
            password_hash=password_hash,
            role=role,
            is_active=is_active,
            is_verified=is_verified,
        )
        self.session.add(user)
        await self.session.flush()
        return user

    async def list_users(
        self,
        *,
        page: int,
        page_size: int,
        role: UserRole | None = None,
    ) -> tuple[list[User], int]:
        base_filter = []
        if role is not None:
            base_filter.append(User.role == role)

        count_stmt = select(func.count()).select_from(User)
        if base_filter:
            count_stmt = count_stmt.where(*base_filter)
        total = int((await self.session.execute(count_stmt)).scalar_one())

        stmt = select(User)
        if base_filter:
            stmt = stmt.where(*base_filter)
        stmt = (
            stmt.order_by(User.created_at.desc(), User.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        return rows, total

