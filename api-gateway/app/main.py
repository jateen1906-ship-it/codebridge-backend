"""CodeBridge API Gateway FastAPI application factory and entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.clients.http_client import GatewayHTTPClient
from app.clients.service_registry import build_service_registry
from app.config import Settings, load_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.middleware.correlation import CorrelationIDMiddleware
from app.routes.health import router as health_router
from app.routes.proxy import build_proxy_router


def create_app(
    settings: Settings | None = None,
    http_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Create and configure the standalone CodeBridge API Gateway application."""
    resolved_settings = settings or load_settings()
    configure_logging(level=resolved_settings.LOG_LEVEL, service_name="api-gateway")
    logger = get_logger("main")

    service_registry = build_service_registry(resolved_settings)
    http_client = GatewayHTTPClient(settings=resolved_settings, transport=http_transport)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        logger.info("Starting CodeBridge API Gateway", extra={"event": "gateway_startup"})
        await http_client.startup()
        try:
            yield
        finally:
            await http_client.shutdown()
            logger.info("Stopped CodeBridge API Gateway", extra={"event": "gateway_shutdown"})

    application = FastAPI(
        title=resolved_settings.APP_NAME,
        version=resolved_settings.APP_VERSION,
        debug=resolved_settings.DEBUG,
        lifespan=lifespan,
    )

    application.state.settings = resolved_settings
    application.state.service_registry = service_registry
    application.state.http_client = http_client

    register_exception_handlers(application)

    # CORSMiddleware added first so CorrelationIDMiddleware wraps it as outermost ASGI layer.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.CORS_ORIGINS,
        allow_credentials=resolved_settings.CORS_ALLOW_CREDENTIALS,
        allow_methods=resolved_settings.CORS_ALLOW_METHODS,
        allow_headers=resolved_settings.CORS_ALLOW_HEADERS,
        expose_headers=["X-Request-ID"],
    )
    application.add_middleware(CorrelationIDMiddleware)

    application.include_router(health_router)
    application.include_router(
        build_proxy_router(service_registry),
        prefix=resolved_settings.API_V1_PREFIX,
    )

    return application


app = create_app()
