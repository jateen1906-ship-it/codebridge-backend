"""CodeBridge Identity Service FastAPI application factory and entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.routes import auth_router, freelancers_router, profile_router
from app.core.config import Settings, load_settings
from app.core.correlation import CorrelationIDMiddleware
from app.core.database import build_async_engine, build_session_factory
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.schemas.common import HealthResponse

_OPENAPI_TAGS = [
    {
        "name": "Health",
        "description": "Service liveness endpoint for Identity Service.",
    },
    {
        "name": "Authentication",
        "description": "Registration, login, JWT refresh token rotation, logout, and current user identity.",
    },
    {
        "name": "Profile",
        "description": "Authenticated user profile retrieval and permitted updates.",
    },
    {
        "name": "Freelancers",
        "description": "Public freelancer discovery and FREELANCER self-profile updates.",
    },
    {
        "name": "Admin Identity Operations",
        "description": "Restricted ADMIN-only user provisioning and status management.",
    },
]


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the standalone CodeBridge Identity Service application."""
    resolved_settings = settings or load_settings()
    configure_logging(level=resolved_settings.LOG_LEVEL, service_name="identity-service")
    logger = get_logger("main")

    engine = build_async_engine(resolved_settings)
    session_factory = build_session_factory(engine)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        logger.info("Starting CodeBridge Identity Service", extra={"event": "service_startup"})
        try:
            yield
        finally:
            await engine.dispose()
            logger.info("Stopped CodeBridge Identity Service", extra={"event": "service_shutdown"})

    application = FastAPI(
        title=resolved_settings.APP_NAME,
        version=resolved_settings.APP_VERSION,
        description=(
            "CodeBridge Identity Service owns user accounts, Argon2 authentication, "
            "signed JWT access/refresh token rotation, RBAC (`OWNER`, `FREELANCER`, `ADMIN`), "
            "user profiles, and public freelancer discovery."
        ),
        debug=resolved_settings.DEBUG,
        openapi_tags=_OPENAPI_TAGS,
        lifespan=lifespan,
    )

    application.state.settings = resolved_settings
    application.state.engine = engine
    application.state.session_factory = session_factory

    register_exception_handlers(application)
    application.add_middleware(CorrelationIDMiddleware)

    @application.get(
        "/health",
        response_model=HealthResponse,
        tags=["Health"],
        summary="Identity Service health check",
    )
    async def health_check() -> HealthResponse:
        return HealthResponse(
            status="healthy",
            service="identity-service",
            version=resolved_settings.APP_VERSION,
        )

    @application.get(
        "/ready",
        tags=["Health"],
        summary="Identity Service readiness check",
    )
    async def readiness_check(request: Request) -> JSONResponse:
        try:
            async with request.app.state.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "status": "ready",
                    "service": "identity-service",
                    "database": "ok",
                },
            )
        except Exception:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={
                    "status": "not_ready",
                    "service": "identity-service",
                    "database": "unavailable",
                },
            )

    api_v1_router = APIRouter(prefix=resolved_settings.API_V1_PREFIX)
    api_v1_router.include_router(auth_router)
    api_v1_router.include_router(profile_router)
    api_v1_router.include_router(freelancers_router)
    application.include_router(api_v1_router)

    return application


app = create_app()
