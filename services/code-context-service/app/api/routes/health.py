"""Liveness, readiness, and metrics snapshot routes for Code Context Service."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.clients.github_client import GitHubServiceClient
from app.clients.identity_client import IdentityClient
from app.clients.project_client import ProjectClient
from app.core.config import Settings
from app.core.logging import MetricsRegistry
from app.schemas.common import HealthResponse, ReadinessResponse
from app.services.vector_store_service import VectorStoreService

router = APIRouter(tags=["Health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness health check",
)
async def health_check(request: Request) -> HealthResponse:
    settings: Settings = request.app.state.settings
    return HealthResponse(status="ok", version=settings.APP_VERSION)


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness check covering PostgreSQL/SQLite DB, ChromaDB, & service clients",
)
async def readiness_check(request: Request):
    db_status: Literal["ok", "error"] = "ok"
    try:
        async with request.app.state.session_factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    vector_store: VectorStoreService | None = getattr(
        request.app.state, "vector_store_service", None
    )
    identity_client: IdentityClient | None = getattr(request.app.state, "identity_client", None)
    project_client: ProjectClient | None = getattr(request.app.state, "project_client", None)
    github_client: GitHubServiceClient | None = getattr(
        request.app.state, "github_service_client", None
    )
    metrics: MetricsRegistry | None = getattr(request.app.state, "metrics", None)

    vec_status: Literal["ok", "error"] = (
        "ok" if vector_store and vector_store.is_ready else "error"
    )
    id_status: Literal["ok", "error"] = (
        "ok" if identity_client and identity_client.is_open else "error"
    )
    proj_status: Literal["ok", "error"] = (
        "ok" if project_client and project_client.is_open else "error"
    )
    gh_status: Literal["ok", "error"] = (
        "ok" if github_client and github_client.is_open else "error"
    )

    is_ready = all(
        s == "ok" for s in (db_status, vec_status, id_status, proj_status, gh_status)
    )
    payload = ReadinessResponse(
        status="ready" if is_ready else "degraded",
        database=db_status,
        vector_store=vec_status,
        identity_client=id_status,
        project_client=proj_status,
        github_client=gh_status,
        metrics=metrics.snapshot() if metrics else {},
    )
    if not is_ready:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=payload.model_dump(mode="json"),
        )
    return payload

