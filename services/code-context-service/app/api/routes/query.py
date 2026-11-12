"""Grounded RAG repository query API route."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.project_client import ProjectClient
from app.core.config import Settings
from app.core.database import get_db_session
from app.core.dependencies import (
    AuthContext,
    get_embedding_service,
    get_llm_client,
    get_metrics_registry,
    get_project_client,
    get_settings,
    get_vector_store_service,
    require_owner_or_freelancer,
)
from app.core.logging import MetricsRegistry
from app.core.security import get_request_id
from app.schemas.common import ErrorResponse
from app.schemas.query import QueryRepositoryRequest, QueryRepositoryResponse
from app.services.embedding_service import EmbeddingService
from app.services.rag_service import LLMClient, RAGService
from app.services.retrieval_service import RetrievalService
from app.services.vector_store_service import VectorStoreService

router = APIRouter(
    prefix="/code-context/projects/{project_id}",
    tags=["Code Context Query"],
)


@router.post(
    "/query",
    response_model=QueryRepositoryResponse,
    summary="Query active repository code context using grounded semantic RAG",
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def query_project_code_context(
    project_id: uuid.UUID,
    payload: QueryRepositoryRequest,
    request: Request,
    auth_ctx: AuthContext = Depends(require_owner_or_freelancer),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    project_client: ProjectClient = Depends(get_project_client),
    embedding_service: EmbeddingService = Depends(get_embedding_service),
    vector_store_service: VectorStoreService = Depends(get_vector_store_service),
    llm_client: LLMClient = Depends(get_llm_client),
    metrics: MetricsRegistry = Depends(get_metrics_registry),
) -> QueryRepositoryResponse:
    retrieval_service = RetrievalService(
        session=session,
        settings=settings,
        project_client=project_client,
        embedding_service=embedding_service,
        vector_store_service=vector_store_service,
        metrics=metrics,
    )
    rag_service = RAGService(
        retrieval_service=retrieval_service,
        llm_client=llm_client,
    )
    return await rag_service.answer_query(
        project_id=project_id,
        query=payload.query,
        top_k=payload.top_k,
        auth_ctx=auth_ctx,
        request_id=get_request_id(request),
    )

