"""Repository helpers for owner GitHub OAuth connections."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import REVOKED_TOKEN_SENTINEL
from app.core.exceptions import ConflictError
from app.models.github_connection import GitHubConnection, GitHubConnectionStatus


class GitHubConnectionRepository:
    """Data-access layer for encrypted owner GitHub OAuth connections."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_active_by_owner_id(self, owner_id: uuid.UUID) -> GitHubConnection | None:
        """Return the owner's single ACTIVE GitHub connection, if present."""
        stmt = select(GitHubConnection).where(
            GitHubConnection.owner_id == owner_id,
            GitHubConnection.status == GitHubConnectionStatus.ACTIVE,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, connection_id: uuid.UUID) -> GitHubConnection | None:
        """Return a GitHub connection by primary key."""
        stmt = select(GitHubConnection).where(GitHubConnection.id == connection_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_or_refresh_active(
        self,
        *,
        owner_id: uuid.UUID,
        github_user_id: int,
        github_login: str,
        encrypted_access_token: str,
        scopes: str,
        now: datetime,
    ) -> GitHubConnection:
        """Create an ACTIVE connection or refresh an existing ACTIVE connection for same GitHub user."""
        existing = await self.get_active_by_owner_id(owner_id)
        if existing is not None:
            if existing.github_user_id != github_user_id:
                raise ConflictError(
                    "An active GitHub connection already exists for a different GitHub account. "
                    "Disconnect the current GitHub connection first.",
                    error_code="GITHUB_CONNECTION_ALREADY_EXISTS",
                )
            existing.github_login = github_login
            existing.encrypted_access_token = encrypted_access_token
            existing.scopes = scopes
            existing.last_validated_at = now
            existing.updated_at = now
            await self._session.flush()
            return existing

        connection = GitHubConnection(
            owner_id=owner_id,
            github_user_id=github_user_id,
            github_login=github_login,
            encrypted_access_token=encrypted_access_token,
            scopes=scopes,
            status=GitHubConnectionStatus.ACTIVE,
            created_at=now,
            updated_at=now,
            last_validated_at=now,
        )
        self._session.add(connection)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ConflictError(
                "An active GitHub connection already exists for this owner.",
                error_code="GITHUB_CONNECTION_ALREADY_EXISTS",
            ) from exc
        return connection

    async def disconnect(
        self,
        connection: GitHubConnection,
        *,
        now: datetime,
        status: GitHubConnectionStatus = GitHubConnectionStatus.DISCONNECTED,
    ) -> GitHubConnection:
        """Disconnect or revoke a GitHub connection and scrub encrypted credential material."""
        connection.status = status
        connection.encrypted_access_token = REVOKED_TOKEN_SENTINEL
        connection.disconnected_at = now
        connection.updated_at = now
        await self._session.flush()
        return connection

