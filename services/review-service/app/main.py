"""Review Service FastAPI application factory and lifespan management."""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import RequestResponseEndpoint

from app.api.routes import build_root_router
from app.clients.identity_client import IdentityClient
from app.clients.project_client import ProjectClient
from app.clients.workspace_client import TelemetryRelayClient, WorkspaceClient
from app.core.config import Settings, load_settings
from app.core.database import build_async_engine, build_session_factory
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger, request_id_ctx

logger = get_logger("main")


def create_app(settings_override: Settings | None = None) -> FastAPI:
    settings = settings_override or load_settings()
    configure_logging(settings.LOG_LEVEL)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("Review Service started", extra={"event": "service_startup"})
        try:
            yield
        finally:
            await app.state.engine.dispose()
            logger.info("Review Service shut down cleanly", extra={"event": "service_shutdown"})

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        debug=settings.DEBUG,
        lifespan=lifespan,
    )

    engine = build_async_engine(settings)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = build_session_factory(engine)
    app.state.identity_client = IdentityClient(
        base_url=settings.IDENTITY_SERVICE_URL,
        timeout_seconds=settings.SERVICE_CLIENT_TIMEOUT_SECONDS,
    )
    app.state.project_client = ProjectClient(
        base_url=settings.PROJECT_SERVICE_URL,
        timeout_seconds=settings.SERVICE_CLIENT_TIMEOUT_SECONDS,
    )
    app.state.workspace_client = WorkspaceClient(
        base_url=settings.WORKSPACE_SERVICE_URL,
        timeout_seconds=settings.SERVICE_CLIENT_TIMEOUT_SECONDS,
    )
    app.state.telemetry_client = TelemetryRelayClient(
        notification_base_url=settings.NOTIFICATION_SERVICE_URL,
        audit_base_url=settings.AUDIT_SERVICE_URL,
        internal_service_key=settings.INTERNAL_SERVICE_KEY,
        timeout_seconds=settings.SERVICE_CLIENT_TIMEOUT_SECONDS,
    )

    @app.middleware("http")
    async def correlation_middleware(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        token = request_id_ctx.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.info(
                "HTTP request completed",
                extra={
                    "event": "http_request",
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                },
            )
            return response
        finally:
            request_id_ctx.reset(token)

    register_exception_handlers(app)
    app.include_router(build_root_router(settings.API_V1_PREFIX))
    return app


app = create_app()
