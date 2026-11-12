"""FastAPI application factory, lifespan wiring, and HTTP server entrypoint for Workspace Service."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.api.routes import health_router, workspaces_router
from app.clients.github_client import GitHubServiceClient
from app.clients.identity_client import IdentityClient
from app.clients.project_client import ProjectClient
from app.core.config import Settings, load_settings
from app.core.database import build_async_engine, build_session_factory
from app.core.exceptions import register_exception_handlers
from app.core.logging import MetricsRegistry, configure_logging, get_logger
from app.core.security import CorrelationIDMiddleware
from app.services.docker_adapter import DockerRuntimeAdapter, build_docker_adapter


def create_app(
    *,
    settings: Settings | None = None,
    identity_transport: httpx.AsyncBaseTransport | None = None,
    project_transport: httpx.AsyncBaseTransport | None = None,
    github_transport: httpx.AsyncBaseTransport | None = None,
    docker_adapter: DockerRuntimeAdapter | None = None,
) -> FastAPI:
    """Create and configure the independent CodeBridge Workspace Service app."""
    cfg = settings or load_settings()
    configure_logging(level=cfg.LOG_LEVEL, service_name="workspace-service")
    logger = get_logger("main")

    engine = build_async_engine(cfg)
    session_factory = build_session_factory(engine)
    metrics = MetricsRegistry()

    identity_client = IdentityClient(cfg, transport=identity_transport)
    project_client = ProjectClient(cfg, transport=project_transport)
    github_service_client = GitHubServiceClient(cfg, transport=github_transport)
    resolved_docker = docker_adapter or build_docker_adapter(cfg)

    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
        await identity_client.startup()
        await project_client.startup()
        await github_service_client.startup()

        logger.info("Workspace Service started", extra={"event": "service_startup"})
        try:
            yield
        finally:
            await github_service_client.close()
            await project_client.close()
            await identity_client.close()
            await engine.dispose()
            logger.info("Workspace Service shut down cleanly", extra={"event": "service_shutdown"})

    application = FastAPI(
        title=cfg.APP_NAME,
        version=cfg.APP_VERSION,
        description=(
            "CodeBridge Workspace Service — isolated sandboxed container workspaces, "
            "verified archive extraction from GitHub Service snapshots, lifecycle management, "
            "and expiry cleanup."
        ),
        docs_url="/docs" if cfg.APP_ENV != "production" else None,
        redoc_url="/redoc" if cfg.APP_ENV != "production" else None,
        openapi_url="/openapi.json" if cfg.APP_ENV != "production" else None,
        lifespan=lifespan,
    )

    application.state.settings = cfg
    application.state.engine = engine
    application.state.session_factory = session_factory
    application.state.metrics = metrics
    application.state.identity_client = identity_client
    application.state.project_client = project_client
    application.state.github_service_client = github_service_client
    application.state.docker_adapter = resolved_docker

    register_exception_handlers(application)
    application.add_middleware(CorrelationIDMiddleware)

    application.include_router(health_router)
    api_prefix = cfg.API_V1_PREFIX.rstrip("/")
    application.include_router(workspaces_router, prefix=api_prefix)

    return application


app = create_app()
