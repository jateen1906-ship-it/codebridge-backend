"""CodeBridge Project Service FastAPI application factory and entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastapi import APIRouter, FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import Settings, load_settings
from app.core.database import build_async_engine, build_session_factory
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.security import CorrelationIDMiddleware
from app.clients.identity_client import IdentityClient
from app.api.routes import assignments_router, projects_router
from app.schemas.common import HealthResponse

_OPENAPI_TAGS = [
    {
        "name": "Health",
        "description": "Service liveness endpoint for Project Service.",
    },
    {
        "name": "Projects",
        "description": "Project creation, discovery, updates, publishing, and cancellation.",
    },
    {
        "name": "Assignments",
        "description": "Owner freelancer selection and active project assignment.",
    },
]


def create_app(
    settings: Settings | None = None,
    *,
    identity_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Create and configure the standalone CodeBridge Project Service application."""
    resolved_settings = settings or load_settings()
    configure_logging(level=resolved_settings.LOG_LEVEL, service_name="project-service")
    logger = get_logger("main")

    engine = build_async_engine(resolved_settings)
    session_factory = build_session_factory(engine)
    identity_client = IdentityClient(resolved_settings, transport=identity_transport)

    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        await identity_client.startup()
        logger.info("Starting CodeBridge Project Service", extra={"event": "service_startup"})
        try:
            yield
        finally:
            await identity_client.close()
            await engine.dispose()
            logger.info("Stopped CodeBridge Project Service", extra={"event": "service_shutdown"})

    application = FastAPI(
        title=resolved_settings.APP_NAME,
        version=resolved_settings.APP_VERSION,
        description=(
            "CodeBridge Project Service owns project creation, 1-to-1 requirements, "
            "role-scoped project discovery, controlled lifecycle transitions, "
            "and verified freelancer assignments."
        ),
        debug=resolved_settings.DEBUG,
        openapi_tags=_OPENAPI_TAGS,
        lifespan=lifespan,
    )

    application.state.settings = resolved_settings
    application.state.engine = engine
    application.state.session_factory = session_factory
    application.state.identity_client = identity_client

    register_exception_handlers(application)
    application.add_middleware(CorrelationIDMiddleware)

    @application.get(
        "/health",
        response_model=HealthResponse,
        tags=["Health"],
        summary="Project Service health check",
    )
    async def health_check() -> HealthResponse:
        return HealthResponse(
            status="healthy",
            service="project-service",
            version=resolved_settings.APP_VERSION,
        )

    @application.get(
        "/ready",
        tags=["Health"],
        summary="Project Service readiness check",
    )
    async def readiness_check(request: Request) -> JSONResponse:
        try:
            async with request.app.state.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "status": "ready",
                    "service": "project-service",
                    "database": "ok",
                },
            )
        except Exception:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={
                    "status": "not_ready",
                    "service": "project-service",
                    "database": "unavailable",
                },
            )

    api_v1_router = APIRouter(prefix=resolved_settings.API_V1_PREFIX)
    api_v1_router.include_router(projects_router)
    api_v1_router.include_router(assignments_router)
    application.include_router(api_v1_router)

    return application


app = create_app()
