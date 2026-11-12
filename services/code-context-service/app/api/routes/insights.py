"""Repository architectural and structural insights routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.project_client import ProjectClient
from app.core.config import Settings
from app.core.database import get_db_session
from app.core.dependencies import (
    AuthContext,
    get_llm_client,
    get_metrics_registry,
    get_project_client,
    get_settings,
    require_any_authenticated,
    require_owner,
)
from app.core.logging import MetricsRegistry
from app.core.security import get_request_id
from app.schemas.common import ErrorResponse
from app.schemas.insights import GenerateInsightsRequest, InsightsEnvelopeResponse
from app.services.insight_service import InsightService
from app.services.rag_service import LLMClient

router = APIRouter(
    prefix="/code-context/projects/{project_id}",
    tags=["Code Context Insights"],
)


def _build_insight_service(
    session: AsyncSession,
    settings: Settings,
    project_client: ProjectClient,
    llm_client: LLMClient,
    metrics: MetricsRegistry,
) -> InsightService:
    return InsightService(
        session=session,
        settings=settings,
        project_client=project_client,
        llm_client=llm_client,
        metrics=metrics,
    )


@router.post(
    "/insights/generate",
    response_model=InsightsEnvelopeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate & persist deterministic + optional AI repository insights (OWNER only)",
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def generate_project_insights(
    project_id: uuid.UUID,
    request: Request,
    payload: GenerateInsightsRequest | None = Body(default=None),
    auth_ctx: AuthContext = Depends(require_owner),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    project_client: ProjectClient = Depends(get_project_client),
    llm_client: LLMClient = Depends(get_llm_client),
    metrics: MetricsRegistry = Depends(get_metrics_registry),
) -> InsightsEnvelopeResponse:
    service = _build_insight_service(
        session,
        settings,
        project_client,
        llm_client,
        metrics,
    )
    return await service.generate_insights(
        project_id=project_id,
        auth_ctx=auth_ctx,
        request_id=get_request_id(request),
        include_ai_summary=payload.include_ai_summary if payload else True,
    )


@router.get(
    "/insights",
    response_model=InsightsEnvelopeResponse,
    summary="Retrieve latest persisted repository insights report",
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def get_project_insights(
    project_id: uuid.UUID,
    request: Request,
    auth_ctx: AuthContext = Depends(require_any_authenticated),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    project_client: ProjectClient = Depends(get_project_client),
    llm_client: LLMClient = Depends(get_llm_client),
    metrics: MetricsRegistry = Depends(get_metrics_registry),
) -> InsightsEnvelopeResponse:
    service = _build_insight_service(
        session,
        settings,
        project_client,
        llm_client,
        metrics,
    )
    return await service.get_latest_insights(
        project_id=project_id,
        auth_ctx=auth_ctx,
        request_id=get_request_id(request),
    )

