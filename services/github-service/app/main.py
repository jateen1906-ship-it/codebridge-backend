"""CodeBridge GitHub Service FastAPI application factory and entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastapi import APIRouter, FastAPI

from app.api.routes import (
    health_router,
    oauth_router,
    project_github_router,
    repositories_router,
)
from app.clients.github_client import GitHubHTTPClient
from app.clients.identity_client import IdentityClient
from app.clients.project_client import ProjectClient
from app.core.config import Settings, load_settings
from app.core.database import build_async_engine, build_session_factory
from app.core.encryption import TokenEncryptionService
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.security import CorrelationIDMiddleware

_OPENAPI_TAGS = [
    {
        "name": "Health",
        "description": "Service liveness and readiness probes for GitHub Service.",
    },
    {
        "name": "GitHub OAuth",
        "description": "GitHub OAuth App authorization initiation, callback, and account disconnect.",
    },
    {
        "name": "GitHub Repositories",
        "description": "Owner repository discovery via decrypted on-demand GitHub tokens.",
    },
    {
        "name": "Project GitHub",
        "description": "Project-to-repository binding, status, disconnect, and read-only access.",
    },
]


def create_app(
    settings: Settings | None = None,
    *,
    identity_transport: httpx.AsyncBaseTransport | None = None,
    project_transport: httpx.AsyncBaseTransport | None = None,
    github_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Create and configure the standalone CodeBridge GitHub Service application."""
    resolved_settings = settings or load_settings()
    configure_logging(level=resolved_settings.LOG_LEVEL, service_name="github-service")
    logger = get_logger("main")

    engine = build_async_engine(resolved_settings)
    session_factory = build_session_factory(engine)
    encryption_service = TokenEncryptionService(resolved_settings.encryption_key_bytes)
    identity_client = IdentityClient(resolved_settings, transport=identity_transport)
    project_client = ProjectClient(resolved_settings, transport=project_transport)
    github_client = GitHubHTTPClient(resolved_settings, transport=github_transport)

    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        await identity_client.startup()
        await project_client.startup()
        await github_client.startup()
        logger.info("Starting CodeBridge GitHub Service", extra={"event": "service_startup"})
        try:
            yield
        finally:
            await github_client.close()
            await project_client.close()
            await identity_client.close()
            await engine.dispose()
            logger.info("Stopped CodeBridge GitHub Service", extra={"event": "service_shutdown"})

    application = FastAPI(
        title=resolved_settings.APP_NAME,
        version=resolved_settings.APP_VERSION,
        description=(
            "CodeBridge GitHub Service manages GitHub OAuth App connections, AES-256-GCM "
            "encrypted credentials, single-use CSRF OAuth state verification, one-repository-per-project "
            "bindings, and bounded read-only repository access."
        ),
        debug=resolved_settings.DEBUG,
        openapi_tags=_OPENAPI_TAGS,
        lifespan=lifespan,
    )

    application.state.settings = resolved_settings
    application.state.engine = engine
    application.state.session_factory = session_factory
    application.state.encryption_service = encryption_service
    application.state.identity_client = identity_client
    application.state.project_client = project_client
    application.state.github_client = github_client

    register_exception_handlers(application)
    application.add_middleware(CorrelationIDMiddleware)

    application.include_router(health_router)

    api_v1_router = APIRouter(prefix=resolved_settings.API_V1_PREFIX)
    api_v1_router.include_router(oauth_router)
    api_v1_router.include_router(repositories_router)
    api_v1_router.include_router(project_github_router)
    application.include_router(api_v1_router)

    return application


app = create_app()
