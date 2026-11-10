"""User query and restricted administrative user-management service."""

from __future__ import annotations

import math
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import EmailAlreadyExistsError, UserNotFoundError
from app.core.logging import get_logger
from app.core.security import hash_password, utc_now
from app.models.user import User, UserRole
from app.repositories.profile_repository import ProfileRepository
from app.repositories.token_repository import TokenRepository
from app.repositories.user_repository import UserRepository
from app.schemas.user import (
    AdminUserCreateRequest,
    AdminUserListResponse,
    AdminUserStatusUpdateRequest,
    UserPublicResponse,
)

logger = get_logger("services.user")


class UserService:
    """Manages user lookup and restricted administrative user provisioning."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.user_repo = UserRepository(session)
        self.profile_repo = ProfileRepository(session)
        self.token_repo = TokenRepository(session)

    async def get_by_id(
        self,
        user_id: uuid.UUID,
        *,
        include_profile: bool = False,
    ) -> User:
        user = await self.user_repo.get_by_id(user_id, include_profile=include_profile)
        if user is None:
            raise UserNotFoundError()
        return user

    async def provision_user_by_admin(self, payload: AdminUserCreateRequest) -> User:
        """Provision any role (including ADMIN) via restricted administrative workflow."""
        existing = await self.user_repo.get_by_email(payload.email)
        if existing is not None:
            raise EmailAlreadyExistsError()

        try:
            user = await self.user_repo.create_user(
                full_name=payload.full_name,
                email=payload.email,
                password_hash=hash_password(payload.password),
                role=payload.role,
                is_active=True,
                is_verified=payload.is_verified,
            )
            if payload.role == UserRole.FREELANCER:
                await self.profile_repo.create_profile(user_id=user.id)
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise EmailAlreadyExistsError() from exc

        logger.info(
            "Administrative user provisioning completed",
            extra={
                "event": "admin_user_provisioned",
                "user_id": str(user.id),
                "role": user.role.value,
            },
        )
        return user

    async def list_users_for_admin(
        self,
        *,
        page: int,
        page_size: int,
        role: UserRole | None = None,
    ) -> AdminUserListResponse:
        users, total = await self.user_repo.list_users(page=page, page_size=page_size, role=role)
        total_pages = math.ceil(total / page_size) if total > 0 else 0
        return AdminUserListResponse(
            items=[UserPublicResponse.model_validate(u) for u in users],
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )

    async def update_user_status_by_admin(
        self,
        *,
        user_id: uuid.UUID,
        payload: AdminUserStatusUpdateRequest,
    ) -> User:
        user = await self.get_by_id(user_id)
        if payload.is_active is not None:
            user.is_active = payload.is_active
            if not payload.is_active:
                await self.token_repo.revoke_all_for_user(
                    user_id=user.id,
                    revoked_at=utc_now(),
                )
        if payload.is_verified is not None:
            user.is_verified = payload.is_verified

        await self.session.commit()
        await self.session.refresh(user)
        return user

