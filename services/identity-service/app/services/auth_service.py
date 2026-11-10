"""Authentication service handling registration, login, token refresh, and logout."""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import (
    EmailAlreadyExistsError,
    ForbiddenError,
    InvalidCredentialsError,
)
from app.core.logging import get_logger
from app.core.security import get_dummy_password_hash, hash_password, verify_password
from app.models.user import User, UserRole
from app.repositories.profile_repository import ProfileRepository
from app.repositories.user_repository import UserRepository
from app.schemas.auth import LoginRequest, RegisterRequest, TokenPairResponse
from app.services.token_service import TokenService

logger = get_logger("services.auth")


class AuthService:
    """Orchestrates public registration, credential login, refresh rotation, and logout."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.user_repo = UserRepository(session)
        self.profile_repo = ProfileRepository(session)
        self.token_service = TokenService(session, settings)

    async def register(self, payload: RegisterRequest) -> User:
        """Register an OWNER or FREELANCER account (never ADMIN)."""
        if payload.role == UserRole.ADMIN:
            raise ForbiddenError("Public registration may only create OWNER or FREELANCER accounts.")

        existing = await self.user_repo.get_by_email(payload.email)
        if existing is not None:
            raise EmailAlreadyExistsError()

        password_digest = hash_password(payload.password)

        try:
            user = await self.user_repo.create_user(
                full_name=payload.full_name,
                email=payload.email,
                password_hash=password_digest,
                role=payload.role,
                is_active=True,
                is_verified=False,
            )
            if payload.role == UserRole.FREELANCER:
                await self.profile_repo.create_profile(user_id=user.id)
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise EmailAlreadyExistsError() from exc

        logger.info(
            "User registered",
            extra={
                "event": "user_registered",
                "user_id": str(user.id),
                "role": user.role.value,
            },
        )
        return user

    async def login(self, payload: LoginRequest) -> TokenPairResponse:
        """Authenticate email/password credentials and return signed access + refresh tokens."""
        user = await self.user_repo.get_by_email(payload.email)
        if user is None:
            verify_password(payload.password, get_dummy_password_hash())
            raise InvalidCredentialsError()

        if not verify_password(payload.password, user.password_hash):
            raise InvalidCredentialsError()

        if not user.is_active:
            raise ForbiddenError("User account is inactive.")

        token_pair, _ = await self.token_service.issue_token_pair(user=user, commit=True)
        logger.info(
            "User logged in",
            extra={
                "event": "user_login",
                "user_id": str(user.id),
                "role": user.role.value,
            },
        )
        return token_pair

    async def refresh(self, refresh_token: str) -> TokenPairResponse:
        """Rotate refresh token and return fresh access + refresh token pair."""
        return await self.token_service.rotate_refresh_token(refresh_token)

    async def logout(self, *, refresh_token: str, authenticated_user_id: uuid.UUID) -> None:
        """Revoke the user's refresh token session."""
        await self.token_service.revoke_refresh_token(
            refresh_token=refresh_token,
            authenticated_user_id=authenticated_user_id,
        )

