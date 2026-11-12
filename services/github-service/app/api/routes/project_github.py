"""Project-scoped GitHub repository connect, status, disconnect, and read-only routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, Response, status
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
    require_any_authenticated,
    require_owner,
    require_owner_or_freelancer,
)
from app.core.encryption import TokenEncryptionService
from app.core.security import get_request_id
from app.schemas.common import ErrorResponse
from app.schemas.project_github import (
    BranchListResponse,
    CommitListResponse,
    ProjectGitHubDisconnectResponse,
    ProjectGitHubStatusResponse,
    ProjectRepositoryMetadata,
    RepositoryContentsResponse,
)
from app.schemas.repository import ConnectRepositoryRequest
from app.services.github_api_service import GitHubAPIService
from app.services.project_github_service import ProjectGitHubService
from app.services.repository_service import GitHubRepositoryService

router = APIRouter(prefix="/projects/{project_id}/github", tags=["Project GitHub"])


def _build_github_api_service(
    settings: Settings,
    encryption_service: TokenEncryptionService,
    github_client: GitHubHTTPClient,
) -> GitHubAPIService:
    return GitHubAPIService(
        settings=settings,
        encryption_service=encryption_service,
        github_client=github_client,
    )


@router.post(
    "/connect",
    response_model=ProjectRepositoryMetadata,
    status_code=status.HTTP_201_CREATED,
    summary="Connect one verified GitHub repository to a CodeBridge project (OWNER only)",
    description=(
        "Verifies project ownership via Project Service, checks no active repository is already "
        "linked to this project, validates repository identity & access against GitHub API, and "
        "stores authoritative repository metadata."
    ),
    responses={
        400: {"model": ErrorResponse, "description": "Repository identity mismatch."},
        401: {"model": ErrorResponse, "description": "Unauthorized."},
        403: {"model": ErrorResponse, "description": "Forbidden (OWNER & project ownership required)."},
        404: {"model": ErrorResponse, "description": "Project, GitHub connection, or repository not found."},
        409: {"model": ErrorResponse, "description": "Project already has an active connected repository."},
        502: {"model": ErrorResponse, "description": "GitHub API failure."},
        503: {"model": ErrorResponse, "description": "Identity or Project Service unavailable."},
    },
)
async def connect_project_repository(
    project_id: uuid.UUID,
    payload: ConnectRepositoryRequest,
    request: Request,
    auth_ctx: AuthContext = Depends(require_owner),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    encryption_service: TokenEncryptionService = Depends(get_encryption_service),
    project_client: ProjectClient = Depends(get_project_client),
    github_client: GitHubHTTPClient = Depends(get_github_client),
) -> ProjectRepositoryMetadata:
    github_api_service = _build_github_api_service(settings, encryption_service, github_client)
    repo_service = GitHubRepositoryService(
        session=session,
        settings=settings,
        project_client=project_client,
        github_api_service=github_api_service,
    )
    return await repo_service.connect_project_repository(
        project_id=project_id,
        payload=payload,
        auth_ctx=auth_ctx,
        request_id=get_request_id(request),
    )


@router.get(
    "/status",
    response_model=ProjectGitHubStatusResponse,
    summary="Get safe connected GitHub repository status for a project",
    description=(
        "Delegates role-scoped project visibility authorization to Project Service and returns "
        "safe repository metadata without exposing OAuth credentials."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized."},
        403: {"model": ErrorResponse, "description": "Forbidden to access this project."},
        404: {"model": ErrorResponse, "description": "Project not found."},
        503: {"model": ErrorResponse, "description": "Identity or Project Service unavailable."},
    },
)
async def get_project_github_status(
    project_id: uuid.UUID,
    request: Request,
    auth_ctx: AuthContext = Depends(require_any_authenticated),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    encryption_service: TokenEncryptionService = Depends(get_encryption_service),
    project_client: ProjectClient = Depends(get_project_client),
    github_client: GitHubHTTPClient = Depends(get_github_client),
) -> ProjectGitHubStatusResponse:
    github_api_service = _build_github_api_service(settings, encryption_service, github_client)
    service = ProjectGitHubService(
        session=session,
        settings=settings,
        project_client=project_client,
        github_api_service=github_api_service,
    )
    return await service.get_project_status(
        project_id=project_id,
        auth_ctx=auth_ctx,
        request_id=get_request_id(request),
    )


@router.delete(
    "/disconnect",
    response_model=ProjectGitHubDisconnectResponse,
    summary="Disconnect a GitHub repository from a CodeBridge project (OWNER only)",
    description=(
        "Disconnects the active repository link for this single project while preserving history, "
        "unless Project Service reports an active freelancer assignment or workflow."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized."},
        403: {"model": ErrorResponse, "description": "Forbidden (OWNER & ownership required)."},
        404: {"model": ErrorResponse, "description": "Project or connected repository not found."},
        409: {"model": ErrorResponse, "description": "Active freelancer workflow blocks disconnect."},
        503: {"model": ErrorResponse, "description": "Identity or Project Service unavailable."},
    },
)
async def disconnect_project_repository(
    project_id: uuid.UUID,
    request: Request,
    auth_ctx: AuthContext = Depends(require_owner),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    encryption_service: TokenEncryptionService = Depends(get_encryption_service),
    project_client: ProjectClient = Depends(get_project_client),
    github_client: GitHubHTTPClient = Depends(get_github_client),
) -> ProjectGitHubDisconnectResponse:
    github_api_service = _build_github_api_service(settings, encryption_service, github_client)
    service = ProjectGitHubService(
        session=session,
        settings=settings,
        project_client=project_client,
        github_api_service=github_api_service,
    )
    return await service.disconnect_project_repository(
        project_id=project_id,
        auth_ctx=auth_ctx,
        request_id=get_request_id(request),
    )


@router.get(
    "/branches",
    response_model=BranchListResponse,
    summary="List branches of the connected project GitHub repository (OWNER only)",
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized."},
        403: {"model": ErrorResponse, "description": "Forbidden (OWNER & ownership required)."},
        404: {"model": ErrorResponse, "description": "Project or connected repository not found."},
        429: {"model": ErrorResponse, "description": "GitHub API rate limit exceeded."},
        502: {"model": ErrorResponse, "description": "GitHub API failure."},
    },
)
async def list_project_branches(
    project_id: uuid.UUID,
    request: Request,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=30, ge=1),
    auth_ctx: AuthContext = Depends(require_owner),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    encryption_service: TokenEncryptionService = Depends(get_encryption_service),
    project_client: ProjectClient = Depends(get_project_client),
    github_client: GitHubHTTPClient = Depends(get_github_client),
) -> BranchListResponse:
    github_api_service = _build_github_api_service(settings, encryption_service, github_client)
    service = ProjectGitHubService(
        session=session,
        settings=settings,
        project_client=project_client,
        github_api_service=github_api_service,
    )
    return await service.list_branches(
        project_id=project_id,
        auth_ctx=auth_ctx,
        page=page,
        per_page=per_page,
        request_id=get_request_id(request),
    )


@router.get(
    "/contents",
    response_model=RepositoryContentsResponse,
    summary="Read bounded file or directory contents of connected repository (OWNER only)",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid path or Git reference."},
        401: {"model": ErrorResponse, "description": "Unauthorized."},
        403: {"model": ErrorResponse, "description": "Forbidden (OWNER & ownership required)."},
        404: {"model": ErrorResponse, "description": "Project, repository, or path not found."},
        429: {"model": ErrorResponse, "description": "GitHub API rate limit exceeded."},
        502: {"model": ErrorResponse, "description": "GitHub API failure."},
    },
)
async def get_project_repository_contents(
    project_id: uuid.UUID,
    request: Request,
    path: str | None = Query(default="", description="Relative file or directory path inside repo."),
    ref: str | None = Query(default=None, description="Optional branch name, tag, or commit SHA."),
    auth_ctx: AuthContext = Depends(require_owner),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    encryption_service: TokenEncryptionService = Depends(get_encryption_service),
    project_client: ProjectClient = Depends(get_project_client),
    github_client: GitHubHTTPClient = Depends(get_github_client),
) -> RepositoryContentsResponse:
    github_api_service = _build_github_api_service(settings, encryption_service, github_client)
    service = ProjectGitHubService(
        session=session,
        settings=settings,
        project_client=project_client,
        github_api_service=github_api_service,
    )
    return await service.get_contents(
        project_id=project_id,
        auth_ctx=auth_ctx,
        raw_path=path,
        raw_ref=ref,
        request_id=get_request_id(request),
    )


@router.get(
    "/commits",
    response_model=CommitListResponse,
    summary="List recent commits of connected project GitHub repository (OWNER only)",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid path or Git reference."},
        401: {"model": ErrorResponse, "description": "Unauthorized."},
        403: {"model": ErrorResponse, "description": "Forbidden (OWNER & ownership required)."},
        404: {"model": ErrorResponse, "description": "Project or connected repository not found."},
        429: {"model": ErrorResponse, "description": "GitHub API rate limit exceeded."},
        502: {"model": ErrorResponse, "description": "GitHub API failure."},
    },
)
async def list_project_commits(
    project_id: uuid.UUID,
    request: Request,
    sha: str | None = Query(default=None, description="Optional branch name or commit SHA filter."),
    path: str | None = Query(default=None, description="Optional repository relative file path filter."),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1),
    auth_ctx: AuthContext = Depends(require_owner),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    encryption_service: TokenEncryptionService = Depends(get_encryption_service),
    project_client: ProjectClient = Depends(get_project_client),
    github_client: GitHubHTTPClient = Depends(get_github_client),
) -> CommitListResponse:
    github_api_service = _build_github_api_service(settings, encryption_service, github_client)
    service = ProjectGitHubService(
        session=session,
        settings=settings,
        project_client=project_client,
        github_api_service=github_api_service,
    )
    return await service.list_commits(
        project_id=project_id,
        auth_ctx=auth_ctx,
        raw_sha=sha,
        raw_path=path,
        page=page,
        per_page=per_page,
        request_id=get_request_id(request),
    )


@router.get(
    "/snapshot",
    summary="Download bounded repository zip snapshot (OWNER or actively assigned FREELANCER)",
    description=(
        "Downloads a read-only zipball archive of the connected project repository for "
        "sandboxed workspace provisioning without ever exposing stored GitHub OAuth tokens."
    ),
    responses={
        200: {"content": {"application/zip": {}}, "description": "Repository zip archive."},
        400: {"model": ErrorResponse, "description": "Invalid Git reference."},
        401: {"model": ErrorResponse, "description": "Unauthorized."},
        403: {"model": ErrorResponse, "description": "Forbidden (owner or active assignee only)."},
        404: {"model": ErrorResponse, "description": "Project or connected repository not found."},
        429: {"model": ErrorResponse, "description": "GitHub API rate limit exceeded."},
        502: {"model": ErrorResponse, "description": "GitHub archive download failure."},
    },
)
async def download_project_repository_snapshot(
    project_id: uuid.UUID,
    request: Request,
    ref: str | None = Query(default=None, description="Optional branch name, tag, or commit SHA."),
    auth_ctx: AuthContext = Depends(require_owner_or_freelancer),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    encryption_service: TokenEncryptionService = Depends(get_encryption_service),
    project_client: ProjectClient = Depends(get_project_client),
    github_client: GitHubHTTPClient = Depends(get_github_client),
) -> Response:
    github_api_service = _build_github_api_service(settings, encryption_service, github_client)
    service = ProjectGitHubService(
        session=session,
        settings=settings,
        project_client=project_client,
        github_api_service=github_api_service,
    )
    archive_bytes, full_name, resolved_ref = await service.download_snapshot(
        project_id=project_id,
        auth_ctx=auth_ctx,
        raw_ref=ref,
        request_id=get_request_id(request),
    )
    safe_slug = full_name.replace("/", "-")
    return Response(
        content=archive_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_slug}.zip"',
            "X-Repository-Full-Name": full_name,
            "X-Repository-Ref": resolved_ref,
        },
    )

