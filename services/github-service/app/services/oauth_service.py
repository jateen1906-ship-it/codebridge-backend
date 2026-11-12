"""GitHub OAuth App authorization initiation, callback processing, and account disconnect."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.github_client import GitHubHTTPClient
from app.clients.project_client import ProjectClient
from app.core.config import Settings
from app.core.dependencies import AuthContext
from app.core.encryption import TokenEncryptionService
from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    GitHubUpstreamError,
    NotFoundError,
)
from app.core.logging import get_logger
from app.core.security import UserRole, generate_oauth_state, hash_oauth_state
from app.models.base import utc_now
from app.repositories.github_connection_repository import GitHubConnectionRepository
from app.repositories.oauth_state_repository import OAuthStateRepository
from app.repositories.project_repository_repository import ProjectRepositoryRepository
from app.schemas.oauth import GitHubConnectionDisconnectResponse, OAuthCallbackResponse

logger = get_logger("services.oauth")


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _split_scopes(scope_str: str) -> list[str]:
    return [part.strip() for part in scope_str.replace(",", " ").split() if part.strip()]


class GitHubOAuthService:
    """Service managing single-use CSRF OAuth states, token encryption, and revocation."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        encryption_service: TokenEncryptionService,
        project_client: ProjectClient,
        github_client: GitHubHTTPClient,
    ) -> None:
        self._session = session
        self._settings = settings
        self._encryption_service = encryption_service
        self._project_client = project_client
        self._github_client = github_client
        self._state_repo = OAuthStateRepository(session)
        self._connection_repo = GitHubConnectionRepository(session)
        self._project_repo_repo = ProjectRepositoryRepository(session)

    async def initiate_oauth(
        self,
        *,
        project_id: uuid.UUID,
        auth_ctx: AuthContext,
        request_id: str,
    ) -> str:
        """Verify project ownership, create hashed single-use OAuth state, return GitHub URL."""
        if auth_ctx.user.role != UserRole.OWNER:
            raise ForbiddenError("Only project owners can initiate GitHub OAuth authorization.")

        project = await self._project_client.get_project(
            project_id,
            bearer_token=auth_ctx.bearer_token,
            request_id=request_id,
        )
        if project.owner_id != auth_ctx.user.id:
            raise ForbiddenError("You can only initiate GitHub OAuth for your own projects.")

        raw_state = generate_oauth_state()
        state_hash = hash_oauth_state(raw_state)
        now = utc_now()
        expires_at = now + timedelta(seconds=self._settings.OAUTH_STATE_EXPIRE_SECONDS)

        await self._state_repo.create(
            state_hash=state_hash,
            owner_id=auth_ctx.user.id,
            project_id=project_id,
            expires_at=expires_at,
        )
        await self._session.commit()

        logger.info(
            "Initiated GitHub OAuth authorization redirect",
            extra={
                "event": "github_oauth_initiated",
                "user_id": str(auth_ctx.user.id),
                "project_id": str(project_id),
            },
        )
        return self._github_client.build_authorize_url(state=raw_state)

    async def handle_callback(
        self,
        *,
        code: str | None,
        state: str | None,
        oauth_error: str | None,
        optional_auth_ctx: AuthContext | None,
        query_keys: set[str],
    ) -> tuple[OAuthCallbackResponse, str | None]:
        """Validate single-use OAuth state, exchange code, encrypt token, and persist connection."""
        if "owner_id" in query_keys or "project_id" in query_keys:
            raise BadRequestError(
                "Caller-supplied owner_id or project_id parameters are not permitted on OAuth callback.",
                error_code="INVALID_OAUTH_CALLBACK",
            )

        if oauth_error and oauth_error.strip():
            raise BadRequestError(
                "GitHub OAuth authorization was denied or failed.",
                error_code="GITHUB_OAUTH_DENIED",
            )

        if not state or not state.strip() or not code or not code.strip():
            raise BadRequestError(
                "Missing required OAuth callback parameters ('code' and 'state').",
                error_code="INVALID_OAUTH_CALLBACK",
            )

        clean_state = state.strip()
        clean_code = code.strip()
        state_hash = hash_oauth_state(clean_state)

        state_record = await self._state_repo.get_by_state_hash(state_hash)
        if state_record is None:
            raise BadRequestError(
                "Invalid or unrecognized OAuth state.",
                error_code="INVALID_OAUTH_STATE",
            )

        if optional_auth_ctx is not None:
            if (
                optional_auth_ctx.user.role != UserRole.OWNER
                or optional_auth_ctx.user.id != state_record.owner_id
            ):
                raise ForbiddenError("OAuth state belongs to a different owner.")

        now = utc_now()
        if state_record.consumed_at is not None:
            raise ConflictError(
                "OAuth state has already been consumed.",
                error_code="OAUTH_STATE_ALREADY_USED",
            )

        if _ensure_utc(state_record.expires_at) <= now:
            raise BadRequestError(
                "OAuth state has expired. Please restart GitHub authorization.",
                error_code="OAUTH_STATE_EXPIRED",
            )

        # Consume single-use state atomically BEFORE exchanging the authorization code with GitHub.
        consumed = await self._state_repo.consume_atomically(
            state_id=state_record.id,
            now=now,
        )
        await self._session.commit()
        if not consumed:
            refreshed = await self._state_repo.get_by_state_hash(state_hash)
            if refreshed is not None and _ensure_utc(refreshed.expires_at) <= utc_now():
                raise BadRequestError(
                    "OAuth state has expired. Please restart GitHub authorization.",
                    error_code="OAUTH_STATE_EXPIRED",
                )
            raise ConflictError(
                "OAuth state has already been consumed.",
                error_code="OAUTH_STATE_ALREADY_USED",
            )

        access_token, granted_scope = await self._github_client.exchange_code_for_token(
            code=clean_code,
        )
        gh_user = await self._github_client.get_authenticated_user(
            access_token=access_token,
        )

        gh_user_id = gh_user.get("id")
        gh_login = gh_user.get("login")
        if (
            not isinstance(gh_user_id, int)
            or isinstance(gh_user_id, bool)
            or gh_user_id < 1
            or not isinstance(gh_login, str)
            or not gh_login.strip()
        ):
            raise GitHubUpstreamError(
                "GitHub authenticated user payload was incomplete.",
                error_code="GITHUB_UPSTREAM_ERROR",
            )

        encrypted_token = self._encryption_service.encrypt(
            access_token,
            owner_id=state_record.owner_id,
        )
        saved_at = utc_now()
        connection = await self._connection_repo.create_or_refresh_active(
            owner_id=state_record.owner_id,
            github_user_id=gh_user_id,
            github_login=gh_login.strip(),
            encrypted_access_token=encrypted_token,
            scopes=granted_scope,
            now=saved_at,
        )
        await self._session.commit()

        logger.info(
            "GitHub OAuth connection established",
            extra={
                "event": "github_oauth_connected",
                "user_id": str(state_record.owner_id),
                "project_id": str(state_record.project_id),
                "github_user_id": gh_user_id,
                "connection_id": str(connection.id),
            },
        )

        response_payload = OAuthCallbackResponse(
            project_id=state_record.project_id,
            connection_id=connection.id,
            github_user_id=connection.github_user_id,
            github_login=connection.github_login,
            scopes=_split_scopes(connection.scopes),
            connected_at=connection.updated_at,
        )

        redirect_url: str | None = None
        if self._settings.FRONTEND_OAUTH_SUCCESS_URL:
            redirect_query = urlencode(
                {
                    "status": "connected",
                    "project_id": str(state_record.project_id),
                    "github_login": connection.github_login,
                }
            )
            redirect_url = f"{self._settings.FRONTEND_OAUTH_SUCCESS_URL}?{redirect_query}"

        return response_payload, redirect_url

    async def disconnect_account(
        self,
        *,
        auth_ctx: AuthContext,
        request_id: str,
    ) -> GitHubConnectionDisconnectResponse:
        """Disconnect owner's GitHub OAuth account and unlink projects lacking active workflows."""
        if auth_ctx.user.role != UserRole.OWNER:
            raise ForbiddenError("Only project owners can disconnect their GitHub connection.")

        connection = await self._connection_repo.get_active_by_owner_id(auth_ctx.user.id)
        if connection is None:
            raise NotFoundError(
                "No active GitHub OAuth connection was found.",
                error_code="GITHUB_CONNECTION_NOT_FOUND",
            )

        active_links = await self._project_repo_repo.list_active_by_connection_id(connection.id)
        for link in active_links:
            project = await self._project_client.get_project(
                link.project_id,
                bearer_token=auth_ctx.bearer_token,
                request_id=request_id,
            )
            if project.owner_id != auth_ctx.user.id:
                raise ForbiddenError("You do not own a project linked to this GitHub connection.")
            if project.has_active_workflow:
                raise ConflictError(
                    "Cannot disconnect GitHub account while linked projects have active freelancer workflows.",
                    error_code="ACTIVE_ASSIGNMENT_CONFLICT",
                )

        now = utc_now()
        disconnected_count = await self._project_repo_repo.disconnect_all_for_connection(
            connection.id,
            now=now,
        )
        await self._connection_repo.disconnect(connection, now=now)
        await self._session.commit()

        logger.info(
            "Disconnected owner GitHub OAuth connection",
            extra={
                "event": "github_connection_disconnected",
                "user_id": str(auth_ctx.user.id),
                "connection_id": str(connection.id),
            },
        )
        return GitHubConnectionDisconnectResponse(
            disconnected_projects_count=disconnected_count,
            disconnected_at=now,
        )
