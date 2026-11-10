"""User profile and public freelancer discovery/update service."""

from __future__ import annotations

import math
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, UserNotFoundError
from app.models.freelancer_profile import AvailabilityStatus, FreelancerProfile
from app.models.user import User, UserRole
from app.repositories.profile_repository import ProfileRepository
from app.repositories.user_repository import UserRepository
from app.schemas.freelancer import (
    FreelancerListResponse,
    FreelancerProfileUpdateRequest,
    FreelancerPublicResponse,
)
from app.schemas.profile import ProfileUpdateRequest


class ProfileService:
    """Business logic for authenticated user profiles and freelancer discovery."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.user_repo = UserRepository(session)
        self.profile_repo = ProfileRepository(session)

    async def get_authenticated_profile(self, user_id: uuid.UUID) -> User:
        user = await self.user_repo.get_by_id(user_id, include_profile=True)
        if user is None:
            raise UserNotFoundError()
        return user

    async def update_authenticated_profile(
        self,
        *,
        user_id: uuid.UUID,
        payload: ProfileUpdateRequest,
    ) -> User:
        user = await self.user_repo.get_by_id(user_id, include_profile=True)
        if user is None:
            raise UserNotFoundError()

        user.full_name = payload.full_name
        await self.session.commit()
        return await self.get_authenticated_profile(user_id)

    async def list_public_freelancers(
        self,
        *,
        page: int,
        page_size: int,
        search: str | None = None,
        skill: str | None = None,
        availability: AvailabilityStatus | None = None,
    ) -> FreelancerListResponse:
        rows, total = await self.profile_repo.list_public_freelancers(
            page=page,
            page_size=page_size,
            search=search,
            skill=skill,
            availability=availability,
        )
        items = [self._to_public_freelancer(profile=p, user=u) for p, u in rows]
        total_pages = math.ceil(total / page_size) if total > 0 else 0
        return FreelancerListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )

    async def get_public_freelancer(self, freelancer_id: uuid.UUID) -> FreelancerPublicResponse:
        row = await self.profile_repo.get_public_by_id(freelancer_id)
        if row is None:
            raise UserNotFoundError("Freelancer profile not found.")
        profile, user = row
        return self._to_public_freelancer(profile=profile, user=user)

    async def update_my_freelancer_profile(
        self,
        *,
        user: User,
        payload: FreelancerProfileUpdateRequest,
    ) -> FreelancerProfile:
        if user.role != UserRole.FREELANCER:
            raise ForbiddenError("Only FREELANCER accounts may modify a freelancer profile.")

        profile = await self.profile_repo.get_by_user_id(user.id)
        if profile is None:
            profile = await self.profile_repo.create_profile(user_id=user.id)

        if payload.bio is not None:
            profile.bio = payload.bio
        if payload.skills is not None:
            profile.skills = payload.skills
        if payload.experience_years is not None:
            profile.experience_years = payload.experience_years
        if payload.availability is not None:
            profile.availability = payload.availability

        await self.session.commit()
        await self.session.refresh(profile)
        return profile

    @staticmethod
    def _to_public_freelancer(
        *,
        profile: FreelancerProfile,
        user: User,
    ) -> FreelancerPublicResponse:
        return FreelancerPublicResponse(
            id=profile.id,
            user_id=user.id,
            full_name=user.full_name,
            bio=profile.bio,
            skills=list(profile.skills or []),
            experience_years=profile.experience_years,
            availability=profile.availability,
            is_verified=user.is_verified,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
        )

