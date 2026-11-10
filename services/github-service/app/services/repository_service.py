"""Repository listing and project repository connection orchestration."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.project_client import ProjectClient
from app.core.config import Settings
from app.core.dependencies import AuthContext
from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.security import UserRole
from app.models.base import utc_now
from app.repositories.github_connection_repository import GitHubConnectionRepository
from app.repositories.project_repository_repository import ProjectRepositoryRepository
from app.schemas.project_github import ProjectRepositoryMetadata
from app.schemas.repository import ConnectRepositoryRequest, GitHubRepositoryListResponse
from app.services.github_api_service import GitHubAPIService

logger = get_logger("services.repository")

_ALLOWED_VISIBILITIES = frozenset({"all", "public", "private"})


class GitHubRepositoryService:
    """Service coordinating owner repository listing and binding one repo to a project."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        project_client: ProjectClient,
        github_api_service: GitHubAPIService,
    ) -> None:
        self._session = session
        self._settings = settings
        self._project_client = project_client
        self._github_api_service = github_api_service
        self._connection_repo = GitHubConnectionRepository(session)
        self._project_repo_repo = ProjectRepositoryRepository(session)

    async def list_owner_repositories(
        self,
        *,
        auth_ctx: AuthContext,
        page: int,
        per_page: int,
        visibility: str,
    ) -> GitHubRepositoryListResponse:
        """List accessible repositories for the authenticated OWNER with strict pagination caps."""
        if auth_ctx.user.role != UserRole.OWNER:
            raise ForbiddenError("Only project owners can list connected GitHub repositories.")

        if page < 1:
            raise ValidationError("page must be greater than or equal to 1.")
        if page > self._settings.GITHUB_API_MAX_PAGES:
            raise ValidationError(
                f"page must not exceed {self._settings.GITHUB_API_MAX_PAGES}."
            )
        if per_page < 1:
            raise ValidationError("per_page must be greater than or equal to 1.")
        capped_per_page = min(per_page, self._settings.GITHUB_API_PAGE_SIZE)

        clean_visibility = visibility.strip().lower()
        if clean_visibility not in _ALLOWED_VISIBILITIES:
            raise ValidationError("visibility must be one of: all, public, private.")

        connection = await self._connection_repo.get_active_by_owner_id(auth_ctx.user.id)
        if connection is None:
            raise NotFoundError(
                "No active GitHub OAuth connection was found. Authorize GitHub first.",
                error_code="GITHUB_CONNECTION_NOT_FOUND",
            )

        return await self._github_api_service.list_owner_repositories(
            connection=connection,
            page=page,
            per_page=capped_per_page,
            visibility=clean_visibility,
        )

    async def connect_project_repository(
        self,
        *,
        project_id: uuid.UUID,
        payload: ConnectRepositoryRequest,
        auth_ctx: AuthContext,
        request_id: str,
    ) -> ProjectRepositoryMetadata:
        """Verify ownership, enforce 1 active repo per project, verify GitHub access, and bind."""
        if auth_ctx.user.role != UserRole.OWNER:
            raise ForbiddenError("Only project owners can connect repositories to projects.")

        project = await self._project_client.get_project(
            project_id,
            bearer_token=auth_ctx.bearer_token,
            request_id=request_id,
        )
        if project.owner_id != auth_ctx.user.id:
            raise ForbiddenError("You can only connect a GitHub repository to your own project.")

        existing_link = await self._project_repo_repo.get_active_by_project_id(project_id)
        if existing_link is not None:
            raise ConflictError(
                "This project already has an active connected GitHub repository.",
                error_code="PROJECT_ALREADY_CONNECTED",
            )

        connection = await self._connection_repo.get_active_by_owner_id(auth_ctx.user.id)
        if connection is None:
            raise NotFoundError(
                "No active GitHub OAuth connection was found. Complete GitHub OAuth first.",
                error_code="GITHUB_CONNECTION_NOT_FOUND",
            )

        verified_repo = await self._github_api_service.verify_repository(
            connection=connection,
            full_name=payload.full_name,
        )
        if (
            verified_repo.github_repository_id != payload.github_repository_id
            or verified_repo.full_name.lower() != payload.full_name.lower()
        ):
            raise BadRequestError(
                "Provided repository ID does not match the GitHub repository metadata.",
                error_code="REPOSITORY_MISMATCH",
            )

        if not verified_repo.permissions.pull:
            raise ForbiddenError(
                "Authenticated GitHub account does not have read access to this repository."
            )

        now = utc_now()
        record = await self._project_repo_repo.create(
            project_id=project_id,
            owner_id=auth_ctx.user.id,
            github_connection_id=connection.id,
            github_repository_id=verified_repo.github_repository_id,
            full_name=verified_repo.full_name,
            repository_name=verified_repo.name,
            owner_login=verified_repo.owner_login,
            is_private=verified_repo.is_private,
            default_branch=verified_repo.default_branch,
            html_url=verified_repo.html_url,
            description=verified_repo.description,
            is_archived=verified_repo.is_archived,
            is_fork=verified_repo.is_fork,
            now=now,
        )
        await self._session.commit()

        logger.info(
            "Connected GitHub repository to CodeBridge project",
            extra={
                "event": "project_repository_connected",
                "user_id": str(auth_ctx.user.id),
                "project_id": str(project_id),
                "repository_full_name": verified_repo.full_name,
            },
        )
        return ProjectRepositoryMetadata.model_validate(record)
