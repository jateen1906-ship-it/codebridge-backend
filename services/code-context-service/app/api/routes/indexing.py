"""Project repository indexing, reindexing, & status API routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.github_client import GitHubServiceClient
from app.clients.project_client import ProjectClient
from app.core.config import Settings
from app.core.database import get_db_session
from app.core.dependencies import (
    AuthContext,
    get_embedding_service,
    get_github_service_client,
    get_metrics_registry,
    get_project_client,
    get_settings,
    get_vector_store_service,
    require_any_authenticated,
    require_owner,
)
from app.core.logging import MetricsRegistry
from app.core.security import get_request_id
from app.schemas.common import ErrorResponse
from app.schemas.indexing import (
    IndexingActionResponse,
    IndexRepositoryRequest,
    IndexStatusResponse,
)
from app.services.embedding_service import EmbeddingService
from app.services.indexing_service import IndexingService
from app.services.vector_store_service import VectorStoreService

router = APIRouter(
    prefix="/code-context/projects/{project_id}",
    tags=["Code Context Indexing"],
)


def _build_indexing_service(
    session: AsyncSession,
    settings: Settings,
    project_client: ProjectClient,
    github_client: GitHubServiceClient,
    embedding_service: EmbeddingService,
    vector_store_service: VectorStoreService,
    metrics: MetricsRegistry,
) -> IndexingService:
    return IndexingService(
        session=session,
        settings=settings,
        project_client=project_client,
        github_client=github_client,
        embedding_service=embedding_service,
        vector_store_service=vector_store_service,
        metrics=metrics,
    )


@router.post(
    "/index",
    response_model=IndexingActionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Index connected GitHub repository for semantic code context (OWNER only)",
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def index_project_repository(
    project_id: uuid.UUID,
    request: Request,
    payload: IndexRepositoryRequest | None = Body(default=None),
    auth_ctx: AuthContext = Depends(require_owner),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    project_client: ProjectClient = Depends(get_project_client),
    github_client: GitHubServiceClient = Depends(get_github_service_client),
    embedding_service: EmbeddingService = Depends(get_embedding_service),
    vector_store_service: VectorStoreService = Depends(get_vector_store_service),
    metrics: MetricsRegistry = Depends(get_metrics_registry),
) -> IndexingActionResponse:
    service = _build_indexing_service(
        session,
        settings,
        project_client,
        github_client,
        embedding_service,
        vector_store_service,
        metrics,
    )
    return await service.start_index(
        project_id=project_id,
        auth_ctx=auth_ctx,
        request_id=get_request_id(request),
        branch=payload.branch if payload else None,
        is_reindex=False,
    )


@router.post(
    "/reindex",
    response_model=IndexingActionResponse,
    status_code=status.HTTP_200_OK,
    summary="Rebuild a new versioned repository index safely (OWNER only)",
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def reindex_project_repository(
    project_id: uuid.UUID,
    request: Request,
    payload: IndexRepositoryRequest | None = Body(default=None),
    auth_ctx: AuthContext = Depends(require_owner),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    project_client: ProjectClient = Depends(get_project_client),
    github_client: GitHubServiceClient = Depends(get_github_service_client),
    embedding_service: EmbeddingService = Depends(get_embedding_service),
    vector_store_service: VectorStoreService = Depends(get_vector_store_service),
    metrics: MetricsRegistry = Depends(get_metrics_registry),
) -> IndexingActionResponse:
    service = _build_indexing_service(
        session,
        settings,
        project_client,
        github_client,
        embedding_service,
        vector_store_service,
        metrics,
    )
    return await service.start_index(
        project_id=project_id,
        auth_ctx=auth_ctx,
        request_id=get_request_id(request),
        branch=payload.branch if payload else None,
        is_reindex=True,
    )


@router.get(
    "/index/status",
    response_model=IndexStatusResponse,
    summary="Get project repository indexing status, counts, & language summary",
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def get_project_index_status(
    project_id: uuid.UUID,
    request: Request,
    auth_ctx: AuthContext = Depends(require_any_authenticated),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    project_client: ProjectClient = Depends(get_project_client),
    github_client: GitHubServiceClient = Depends(get_github_service_client),
    embedding_service: EmbeddingService = Depends(get_embedding_service),
    vector_store_service: VectorStoreService = Depends(get_vector_store_service),
    metrics: MetricsRegistry = Depends(get_metrics_registry),
) -> IndexStatusResponse:
    service = _build_indexing_service(
        session,
        settings,
        project_client,
        github_client,
        embedding_service,
        vector_store_service,
        metrics,
    )
    return await service.get_status(
        project_id=project_id,
        auth_ctx=auth_ctx,
        request_id=get_request_id(request),
    )

