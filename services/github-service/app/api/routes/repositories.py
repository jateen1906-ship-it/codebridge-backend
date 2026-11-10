"""Owner GitHub repository discovery route."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.github_client import GitHubHTTPClient
from app.clients.project_client import ProjectClient
from app.core.config import Settings
from app.core.database import get_db_session
from app.core.dependencies import (
    AuthContext,
    get_encryption_service,
    get_github_client,
    get_project_client,
    get_settings,
    require_owner,
)
from app.core.encryption import TokenEncryptionService
from app.schemas.common import ErrorResponse
from app.schemas.repository import GitHubRepositoryListResponse
from app.services.github_api_service import GitHubAPIService
from app.services.repository_service import GitHubRepositoryService

router = APIRouter(prefix="/github", tags=["GitHub Repositories"])


@router.get(
    "/repositories",
    response_model=GitHubRepositoryListResponse,
    summary="List accessible GitHub repositories for connected owner (OWNER only)",
    description=(
        "Decrypts the authenticated owner's active GitHub OAuth token on demand and returns "
        "a paginated list of accessible repositories with normalized safe metadata."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized."},
        403: {"model": ErrorResponse, "description": "Forbidden (OWNER role required)."},
        404: {"model": ErrorResponse, "description": "No active GitHub OAuth connection found."},
        422: {"model": ErrorResponse, "description": "Invalid pagination or visibility parameter."},
        429: {"model": ErrorResponse, "description": "GitHub API rate limit exceeded."},
        502: {"model": ErrorResponse, "description": "GitHub API upstream error."},
        504: {"model": ErrorResponse, "description": "GitHub API timeout."},
    },
)
async def list_owner_repositories(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1),
    visibility: str = Query(default="all", description="Repository visibility filter: all, public, private."),
    auth_ctx: AuthContext = Depends(require_owner),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    encryption_service: TokenEncryptionService = Depends(get_encryption_service),
    project_client: ProjectClient = Depends(get_project_client),
    github_client: GitHubHTTPClient = Depends(get_github_client),
) -> GitHubRepositoryListResponse:
    github_api_service = GitHubAPIService(
        settings=settings,
        encryption_service=encryption_service,
        github_client=github_client,
    )
    repo_service = GitHubRepositoryService(
        session=session,
        settings=settings,
        project_client=project_client,
        github_api_service=github_api_service,
    )
    return await repo_service.list_owner_repositories(
        auth_ctx=auth_ctx,
        page=page,
        per_page=per_page,
        visibility=visibility,
    )

