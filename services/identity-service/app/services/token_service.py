"""Token issuance, rotation, revocation, and reuse-detection business logic."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import (
    ForbiddenError,
    InvalidTokenError,
    TokenExpiredError,
    TokenRevokedError,
)
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    ensure_utc,
    hash_jti,
    utc_now,
)
from app.models.user import User
from app.repositories.token_repository import TokenRepository
from app.repositories.user_repository import UserRepository
from app.schemas.auth import TokenPairResponse
from app.schemas.user import UserPublicResponse

logger = get_logger("services.token")


class TokenService:
    """Service managing JWT creation, hashed refresh-token storage, rotation, and revocation."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.token_repo = TokenRepository(session)
        self.user_repo = UserRepository(session)

    async def issue_token_pair(
        self,
        *,
        user: User,
        family_id: uuid.UUID | None = None,
        commit: bool = True,
    ) -> tuple[TokenPairResponse, uuid.UUID]:
        """Issue a new signed access + refresh JWT pair and persist hashed refresh JTI."""
        active_family = family_id or uuid.uuid4()
        access_jwt, _ = create_access_token(user_id=user.id, settings=self.settings)
        refresh_jwt, refresh_claims = create_refresh_token(user_id=user.id, settings=self.settings)

        record = await self.token_repo.create_token(
            user_id=user.id,
            family_id=active_family,
            jti_hash=hash_jti(refresh_claims.jti),
            expires_at=refresh_claims.exp,
        )
        if commit:
            await self.session.commit()

        response = TokenPairResponse(
            access_token=access_jwt,
            refresh_token=refresh_jwt,
            token_type="bearer",
            expires_in=self.settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            refresh_expires_in=self.settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
            user=UserPublicResponse.model_validate(user),
        )
        return response, record.id

    async def rotate_refresh_token(self, refresh_token: str) -> TokenPairResponse:
        """Validate a refresh token, detect reuse, and transactionally rotate it."""
        claims = decode_token(
            token=refresh_token,
            expected_type="refresh",
            settings=self.settings,
        )
        jti_digest = hash_jti(claims.jti)
        record = await self.token_repo.get_by_jti_hash(jti_digest, for_update=True)
        if record is None:
            raise InvalidTokenError("Refresh token is not recognized.")

        now = utc_now()

        # Reuse detection: revoked or already replaced refresh token replayed.
        if record.revoked_at is not None or record.replaced_by_token_id is not None:
            await self.token_repo.revoke_family(family_id=record.family_id, revoked_at=now)
            await self.session.commit()
            logger.warning(
                "Refresh token reuse detected; revoked entire token family",
                extra={
                    "event": "refresh_token_reuse_detected",
                    "user_id": str(record.user_id),
                    "error_code": "TOKEN_REVOKED",
                },
            )
            raise TokenRevokedError("Refresh token has already been used or revoked.")

        if ensure_utc(record.expires_at) <= now:
            await self.token_repo.revoke_token(record=record, revoked_at=now)
            await self.session.commit()
            raise TokenExpiredError("Refresh token has expired.")

        user = await self.user_repo.get_by_id(record.user_id)
        if user is None:
            await self.token_repo.revoke_family(family_id=record.family_id, revoked_at=now)
            await self.session.commit()
            raise InvalidTokenError("Refresh token subject no longer exists.")

        if not user.is_active:
            await self.token_repo.revoke_family(family_id=record.family_id, revoked_at=now)
            await self.session.commit()
            raise ForbiddenError("User account is inactive.")

        token_pair, new_record_id = await self.issue_token_pair(
            user=user,
            family_id=record.family_id,
            commit=False,
        )
        await self.token_repo.mark_rotated(
            record=record,
            replaced_by_token_id=new_record_id,
            revoked_at=now,
        )
        await self.session.commit()
        return token_pair

    async def revoke_refresh_token(
        self,
        *,
        refresh_token: str,
        authenticated_user_id: uuid.UUID,
    ) -> None:
        """Revoke the active refresh token session for an authenticated logout request."""
        claims = decode_token(
            token=refresh_token,
            expected_type="refresh",
            settings=self.settings,
        )
        if claims.sub != authenticated_user_id:
            raise ForbiddenError("Cannot revoke a refresh token belonging to another user.")

        record = await self.token_repo.get_by_jti_hash(hash_jti(claims.jti), for_update=True)
        if record is None:
            raise InvalidTokenError("Refresh token is not recognized.")

        if record.revoked_at is not None or record.replaced_by_token_id is not None:
            raise TokenRevokedError("Refresh token has already been revoked.")

        now = utc_now()
        await self.token_repo.revoke_family(family_id=record.family_id, revoked_at=now)
        await self.session.commit()

