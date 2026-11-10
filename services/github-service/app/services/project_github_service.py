"""Project-scoped GitHub status, safe disconnection, and read-only repo access service."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.project_client import ProjectClient, ProjectSummary
from app.core.config import Settings
from app.core.dependencies import AuthContext
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.security import UserRole, normalize_repo_path, validate_git_ref
from app.models.base import utc_now
from app.models.github_connection import GitHubConnection, GitHubConnectionStatus
from app.models.project_repository import ProjectRepository
from app.repositories.github_connection_repository import GitHubConnectionRepository
from app.repositories.project_repository_repository import ProjectRepositoryRepository
from app.schemas.project_github import (
    BranchListResponse,
    CommitListResponse,
    ProjectGitHubDisconnectResponse,
    ProjectGitHubStatusResponse,
    ProjectRepositoryMetadata,
    RepositoryContentsResponse,
)
from app.services.github_api_service import GitHubAPIService

logger = get_logger("services.project_github")


class ProjectGitHubService:
    """Service coordinating project GitHub status, disconnection, and read-only operations."""

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

    async def _require_owned_project(
        self,
        *,
        project_id: uuid.UUID,
        auth_ctx: AuthContext,
        request_id: str,
    ) -> ProjectSummary:
        if auth_ctx.user.role != UserRole.OWNER:
            raise ForbiddenError("Only the project owner may perform this repository action.")
        project = await self._project_client.get_project(
            project_id,
            bearer_token=auth_ctx.bearer_token,
            request_id=request_id,
        )
        if project.owner_id != auth_ctx.user.id:
            raise ForbiddenError("You do not own this project.")
        return project

    async def _require_owned_active_repo_and_connection(
        self,
        *,
        project_id: uuid.UUID,
        auth_ctx: AuthContext,
        request_id: str,
    ) -> tuple[GitHubConnection, ProjectRepository]:
        await self._require_owned_project(
            project_id=project_id,
            auth_ctx=auth_ctx,
            request_id=request_id,
        )
        repo_link = await self._project_repo_repo.get_active_by_project_id(project_id)
        if repo_link is None:
            raise NotFoundError(
                "No active GitHub repository is connected to this project.",
                error_code="PROJECT_REPOSITORY_NOT_FOUND",
            )
        connection = await self._connection_repo.get_by_id(repo_link.github_connection_id)
        if (
            connection is None
            or connection.status != GitHubConnectionStatus.ACTIVE
            or connection.owner_id != auth_ctx.user.id
        ):
            raise NotFoundError(
                "No active GitHub OAuth connection was found for this project.",
                error_code="GITHUB_CONNECTION_NOT_FOUND",
            )
        return connection, repo_link

    async def get_project_status(
        self,
        *,
        project_id: uuid.UUID,
        auth_ctx: AuthContext,
        request_id: str,
    ) -> ProjectGitHubStatusResponse:
        """Return safe connected repository metadata if Project Service authorizes caller access."""
        await self._project_client.get_project(
            project_id,
            bearer_token=auth_ctx.bearer_token,
            request_id=request_id,
        )
        active_repo = await self._project_repo_repo.get_active_by_project_id(project_id)
        if active_repo is None:
            return ProjectGitHubStatusResponse(
                project_id=project_id,
                connected=False,
                connection_status="NOT_CONNECTED",
                repository=None,
            )
        return ProjectGitHubStatusResponse(
            project_id=project_id,
            connected=True,
            connection_status="ACTIVE",
            repository=ProjectRepositoryMetadata.model_validate(active_repo),
        )

    async def disconnect_project_repository(
        self,
        *,
        project_id: uuid.UUID,
        auth_ctx: AuthContext,
        request_id: str,
    ) -> ProjectGitHubDisconnectResponse:
        """Disconnect only this project's active repository link unless active workflow blocks it."""
        project = await self._require_owned_project(
            project_id=project_id,
            auth_ctx=auth_ctx,
            request_id=request_id,
        )
        active_repo = await self._project_repo_repo.get_active_by_project_id(project_id)
        if active_repo is None:
            raise NotFoundError(
                "No active GitHub repository is connected to this project.",
                error_code="PROJECT_REPOSITORY_NOT_FOUND",
            )

        if project.has_active_workflow:
            raise ConflictError(
                "Cannot disconnect repository while the project has an active assignment or workflow.",
                error_code="ACTIVE_ASSIGNMENT_CONFLICT",
            )

        now = utc_now()
        await self._project_repo_repo.disconnect_project(active_repo, now=now)
        await self._session.commit()

        logger.info(
            "Disconnected GitHub repository from CodeBridge project",
            extra={
                "event": "project_repository_disconnected",
                "user_id": str(auth_ctx.user.id),
                "project_id": str(project_id),
                "repository_full_name": active_repo.full_name,
            },
        )
        return ProjectGitHubDisconnectResponse(
            project_id=project_id,
            disconnected_at=now,
        )

    def _validate_pagination(self, *, page: int, per_page: int) -> tuple[int, int]:
        if page < 1:
            raise ValidationError("page must be greater than or equal to 1.")
        if page > self._settings.GITHUB_API_MAX_PAGES:
            raise ValidationError(
                f"page must not exceed {self._settings.GITHUB_API_MAX_PAGES}."
            )
        if per_page < 1:
            raise ValidationError("per_page must be greater than or equal to 1.")
        return page, min(per_page, self._settings.GITHUB_API_PAGE_SIZE)

    async def list_branches(
        self,
        *,
        project_id: uuid.UUID,
        auth_ctx: AuthContext,
        page: int,
        per_page: int,
        request_id: str,
    ) -> BranchListResponse:
        """Read-only branch list for an owner's connected project repository."""
        valid_page, capped_per_page = self._validate_pagination(page=page, per_page=per_page)
        connection, repo_link = await self._require_owned_active_repo_and_connection(
            project_id=project_id,
            auth_ctx=auth_ctx,
            request_id=request_id,
        )
        return await self._github_api_service.list_branches(
            project_id=project_id,
            connection=connection,
            full_name=repo_link.full_name,
            page=valid_page,
            per_page=capped_per_page,
        )

    async def get_contents(
        self,
        *,
        project_id: uuid.UUID,
        auth_ctx: AuthContext,
        raw_path: str | None,
        raw_ref: str | None,
        request_id: str,
    ) -> RepositoryContentsResponse:
        """Read-only repository file/directory contents after path and ref validation."""
        normalized_path = normalize_repo_path(raw_path)
        validated_ref = validate_git_ref(raw_ref)
        connection, repo_link = await self._require_owned_active_repo_and_connection(
            project_id=project_id,
            auth_ctx=auth_ctx,
            request_id=request_id,
        )
        return await self._github_api_service.get_contents(
            project_id=project_id,
            connection=connection,
            full_name=repo_link.full_name,
            repo_path=normalized_path,
            ref=validated_ref,
        )

    async def list_commits(
        self,
        *,
        project_id: uuid.UUID,
        auth_ctx: AuthContext,
        raw_sha: str | None,
        raw_path: str | None,
        page: int,
        per_page: int,
        request_id: str,
    ) -> CommitListResponse:
        """Read-only commit listing after ref, path, and pagination validation."""
        valid_page, capped_per_page = self._validate_pagination(page=page, per_page=per_page)
        validated_sha = validate_git_ref(raw_sha)
        normalized_path = normalize_repo_path(raw_path) if raw_path is not None else None
        effective_path = normalized_path if normalized_path else None

        connection, repo_link = await self._require_owned_active_repo_and_connection(
            project_id=project_id,
            auth_ctx=auth_ctx,
            request_id=request_id,
        )
        return await self._github_api_service.list_commits(
            project_id=project_id,
            connection=connection,
            full_name=repo_link.full_name,
            page=valid_page,
            per_page=capped_per_page,
            sha=validated_sha,
            repo_path=effective_path,
        )
