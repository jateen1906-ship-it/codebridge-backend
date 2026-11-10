"""Async SQLAlchemy repository for FreelancerProfile persistence and discovery."""

from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.freelancer_profile import AvailabilityStatus, FreelancerProfile
from app.models.user import User, UserRole


class ProfileRepository:
    """Data access layer for FreelancerProfile entities."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_user_id(self, user_id: uuid.UUID) -> FreelancerProfile | None:
        stmt = select(FreelancerProfile).where(FreelancerProfile.user_id == user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_profile(self, *, user_id: uuid.UUID) -> FreelancerProfile:
        profile = FreelancerProfile(
            user_id=user_id,
            bio=None,
            skills=[],
            experience_years=0,
            availability=AvailabilityStatus.AVAILABLE,
        )
        self.session.add(profile)
        await self.session.flush()
        return profile

    async def get_public_by_id(
        self,
        freelancer_id: uuid.UUID,
    ) -> tuple[FreelancerProfile, User] | None:
        stmt = (
            select(FreelancerProfile, User)
            .join(User, FreelancerProfile.user_id == User.id)
            .where(
                User.role == UserRole.FREELANCER,
                User.is_active.is_(True),
                or_(
                    FreelancerProfile.id == freelancer_id,
                    FreelancerProfile.user_id == freelancer_id,
                ),
            )
        )
        result = await self.session.execute(stmt)
        row = result.first()
        if row is None:
            return None
        return row[0], row[1]

    async def list_public_freelancers(
        self,
        *,
        page: int,
        page_size: int,
        search: str | None = None,
        skill: str | None = None,
        availability: AvailabilityStatus | None = None,
    ) -> tuple[list[tuple[FreelancerProfile, User]], int]:
        conditions = [
            User.role == UserRole.FREELANCER,
            User.is_active.is_(True),
        ]
        if search and search.strip():
            term = f"%{search.strip()}%"
            conditions.append(User.full_name.ilike(term))
        if availability is not None:
            conditions.append(FreelancerProfile.availability == availability)

        base_stmt = (
            select(FreelancerProfile, User)
            .join(User, FreelancerProfile.user_id == User.id)
            .where(*conditions)
            .order_by(FreelancerProfile.created_at.desc(), FreelancerProfile.id.asc())
        )

        if skill and skill.strip():
            # Case-insensitive exact skill tag matching compatible across PostgreSQL and SQLite.
            target_skill = skill.strip().lower()
            rows = (await self.session.execute(base_stmt)).all()
            filtered = [
                (profile, user)
                for profile, user in rows
                if any(str(tag).strip().lower() == target_skill for tag in (profile.skills or []))
            ]
            total = len(filtered)
            start = (page - 1) * page_size
            return filtered[start : start + page_size], total

        count_stmt = (
            select(func.count())
            .select_from(FreelancerProfile)
            .join(User, FreelancerProfile.user_id == User.id)
            .where(*conditions)
        )
        total = int((await self.session.execute(count_stmt)).scalar_one())

        paged_stmt = base_stmt.offset((page - 1) * page_size).limit(page_size)
        rows = [(row[0], row[1]) for row in (await self.session.execute(paged_stmt)).all()]
        return rows, total

