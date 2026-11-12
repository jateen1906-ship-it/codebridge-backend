"""Workspace provisioning, lifecycle, refresh, logs, and cleanup routes under /api/v1/workspace."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.github_client import GitHubServiceClient
from app.clients.project_client import ProjectClient
from app.core.config import Settings
from app.core.database import get_db_session
from app.core.dependencies import (
    AuthContext,
    get_docker_adapter,
    get_github_service_client,
    get_metrics_registry,
    get_project_client,
    get_settings,
    require_any_authenticated,
    require_owner_or_admin,
    require_owner_or_freelancer,
)
from app.core.logging import MetricsRegistry
from app.core.security import get_request_id
from app.schemas.common import ErrorResponse
from app.schemas.workspace import (
    WorkspaceCleanupResponse,
    WorkspaceDetailResponse,
    WorkspaceListResponse,
    WorkspaceLogsResponse,
    WorkspaceProvisionRequest,
    WorkspaceRefreshRequest,
    WorkspaceResponse,
)
from app.services.docker_adapter import DockerRuntimeAdapter
from app.services.workspace_service import WorkspaceService

router = APIRouter(prefix="/workspace", tags=["Workspace"])


def _build_service(
    *,
    session: AsyncSession,
    settings: Settings,
    project_client: ProjectClient,
    github_client: GitHubServiceClient,
    docker_adapter: DockerRuntimeAdapter,
    metrics: MetricsRegistry,
) -> WorkspaceService:
    return WorkspaceService(
        session=session,
        settings=settings,
        project_client=project_client,
        github_client=github_client,
        docker_adapter=docker_adapter,
        metrics=metrics,
    )


@router.post(
    "/projects/{project_id}",
    response_model=WorkspaceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Provision an isolated workspace for an assigned project",
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def provision_workspace(
    project_id: uuid.UUID,
    request: Request,
    payload: WorkspaceProvisionRequest = WorkspaceProvisionRequest(),
    auth_ctx: AuthContext = Depends(require_owner_or_freelancer),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    project_client: ProjectClient = Depends(get_project_client),
    github_client: GitHubServiceClient = Depends(get_github_service_client),
    docker_adapter: DockerRuntimeAdapter = Depends(get_docker_adapter),
    metrics: MetricsRegistry = Depends(get_metrics_registry),
) -> WorkspaceResponse:
    service = _build_service(
        session=session,
        settings=settings,
        project_client=project_client,
        github_client=github_client,
        docker_adapter=docker_adapter,
        metrics=metrics,
    )
    return await service.provision_workspace(
        project_id=project_id,
        payload=payload,
        auth_ctx=auth_ctx,
        request_id=get_request_id(request),
    )


@router.get(
    "/projects/{project_id}",
    response_model=WorkspaceListResponse,
    summary="List workspaces belonging to a project",
)
async def list_project_workspaces(
    project_id: uuid.UUID,
    request: Request,
    auth_ctx: AuthContext = Depends(require_any_authenticated),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    project_client: ProjectClient = Depends(get_project_client),
    github_client: GitHubServiceClient = Depends(get_github_service_client),
    docker_adapter: DockerRuntimeAdapter = Depends(get_docker_adapter),
    metrics: MetricsRegistry = Depends(get_metrics_registry),
) -> WorkspaceListResponse:
    service = _build_service(
        session=session,
        settings=settings,
        project_client=project_client,
        github_client=github_client,
        docker_adapter=docker_adapter,
        metrics=metrics,
    )
    return await service.list_project_workspaces(
        project_id=project_id,
        auth_ctx=auth_ctx,
        request_id=get_request_id(request),
    )


@router.post(
    "/cleanup-expired",
    response_model=WorkspaceCleanupResponse,
    summary="Expire and clean up workspaces whose TTL has elapsed (OWNER or ADMIN)",
)
async def cleanup_expired_workspaces(
    auth_ctx: AuthContext = Depends(require_owner_or_admin),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    project_client: ProjectClient = Depends(get_project_client),
    github_client: GitHubServiceClient = Depends(get_github_service_client),
    docker_adapter: DockerRuntimeAdapter = Depends(get_docker_adapter),
    metrics: MetricsRegistry = Depends(get_metrics_registry),
) -> WorkspaceCleanupResponse:
    service = _build_service(
        session=session,
        settings=settings,
        project_client=project_client,
        github_client=github_client,
        docker_adapter=docker_adapter,
        metrics=metrics,
    )
    return await service.cleanup_expired_workspaces(auth_ctx=auth_ctx)


@router.get(
    "/{workspace_id}",
    response_model=WorkspaceDetailResponse,
    summary="Get workspace metadata and lifecycle history",
)
async def get_workspace(
    workspace_id: uuid.UUID,
    request: Request,
    auth_ctx: AuthContext = Depends(require_any_authenticated),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    project_client: ProjectClient = Depends(get_project_client),
    github_client: GitHubServiceClient = Depends(get_github_service_client),
    docker_adapter: DockerRuntimeAdapter = Depends(get_docker_adapter),
    metrics: MetricsRegistry = Depends(get_metrics_registry),
) -> WorkspaceDetailResponse:
    service = _build_service(
        session=session,
        settings=settings,
        project_client=project_client,
        github_client=github_client,
        docker_adapter=docker_adapter,
        metrics=metrics,
    )
    return await service.get_workspace_detail(
        workspace_id=workspace_id,
        auth_ctx=auth_ctx,
        request_id=get_request_id(request),
    )


@router.post(
    "/{workspace_id}/start",
    response_model=WorkspaceResponse,
    summary="Start a provisioned or stopped workspace container",
)
async def start_workspace(
    workspace_id: uuid.UUID,
    request: Request,
    auth_ctx: AuthContext = Depends(require_owner_or_freelancer),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    project_client: ProjectClient = Depends(get_project_client),
    github_client: GitHubServiceClient = Depends(get_github_service_client),
    docker_adapter: DockerRuntimeAdapter = Depends(get_docker_adapter),
    metrics: MetricsRegistry = Depends(get_metrics_registry),
) -> WorkspaceResponse:
    service = _build_service(
        session=session,
        settings=settings,
        project_client=project_client,
        github_client=github_client,
        docker_adapter=docker_adapter,
        metrics=metrics,
    )
    return await service.start_workspace(
        workspace_id=workspace_id,
        auth_ctx=auth_ctx,
        request_id=get_request_id(request),
    )


@router.post(
    "/{workspace_id}/stop",
    response_model=WorkspaceResponse,
    summary="Stop a ready or running workspace container",
)
async def stop_workspace(
    workspace_id: uuid.UUID,
    request: Request,
    auth_ctx: AuthContext = Depends(require_owner_or_freelancer),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    project_client: ProjectClient = Depends(get_project_client),
    github_client: GitHubServiceClient = Depends(get_github_service_client),
    docker_adapter: DockerRuntimeAdapter = Depends(get_docker_adapter),
    metrics: MetricsRegistry = Depends(get_metrics_registry),
) -> WorkspaceResponse:
    service = _build_service(
        session=session,
        settings=settings,
        project_client=project_client,
        github_client=github_client,
        docker_adapter=docker_adapter,
        metrics=metrics,
    )
    return await service.stop_workspace(
        workspace_id=workspace_id,
        auth_ctx=auth_ctx,
        request_id=get_request_id(request),
    )


@router.post(
    "/{workspace_id}/refresh",
    response_model=WorkspaceResponse,
    summary="Refresh workspace repository snapshot via GitHub Service",
)
async def refresh_workspace(
    workspace_id: uuid.UUID,
    request: Request,
    payload: WorkspaceRefreshRequest = WorkspaceRefreshRequest(),
    auth_ctx: AuthContext = Depends(require_owner_or_freelancer),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    project_client: ProjectClient = Depends(get_project_client),
    github_client: GitHubServiceClient = Depends(get_github_service_client),
    docker_adapter: DockerRuntimeAdapter = Depends(get_docker_adapter),
    metrics: MetricsRegistry = Depends(get_metrics_registry),
) -> WorkspaceResponse:
    service = _build_service(
        session=session,
        settings=settings,
        project_client=project_client,
        github_client=github_client,
        docker_adapter=docker_adapter,
        metrics=metrics,
    )
    return await service.refresh_workspace(
        workspace_id=workspace_id,
        payload=payload,
        auth_ctx=auth_ctx,
        request_id=get_request_id(request),
    )


@router.get(
    "/{workspace_id}/logs",
    response_model=WorkspaceLogsResponse,
    summary="Fetch bounded sanitized container logs for a workspace",
)
async def get_workspace_logs(
    workspace_id: uuid.UUID,
    request: Request,
    tail: int = Query(default=100, ge=1, le=500),
    auth_ctx: AuthContext = Depends(require_any_authenticated),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    project_client: ProjectClient = Depends(get_project_client),
    github_client: GitHubServiceClient = Depends(get_github_service_client),
    docker_adapter: DockerRuntimeAdapter = Depends(get_docker_adapter),
    metrics: MetricsRegistry = Depends(get_metrics_registry),
) -> WorkspaceLogsResponse:
    service = _build_service(
        session=session,
        settings=settings,
        project_client=project_client,
        github_client=github_client,
        docker_adapter=docker_adapter,
        metrics=metrics,
    )
    return await service.get_logs(
        workspace_id=workspace_id,
        tail=tail,
        auth_ctx=auth_ctx,
        request_id=get_request_id(request),
    )


@router.delete(
    "/{workspace_id}",
    response_model=WorkspaceResponse,
    summary="Destroy workspace container and clean up extracted snapshot storage",
)
async def destroy_workspace(
    workspace_id: uuid.UUID,
    request: Request,
    auth_ctx: AuthContext = Depends(require_owner_or_freelancer),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    project_client: ProjectClient = Depends(get_project_client),
    github_client: GitHubServiceClient = Depends(get_github_service_client),
    docker_adapter: DockerRuntimeAdapter = Depends(get_docker_adapter),
    metrics: MetricsRegistry = Depends(get_metrics_registry),
) -> WorkspaceResponse:
    service = _build_service(
        session=session,
        settings=settings,
        project_client=project_client,
        github_client=github_client,
        docker_adapter=docker_adapter,
        metrics=metrics,
    )
    return await service.destroy_workspace(
        workspace_id=workspace_id,
        auth_ctx=auth_ctx,
        request_id=get_request_id(request),
    )

