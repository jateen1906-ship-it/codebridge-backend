"""FastAPI application factory, lifespan wiring, and HTTP server entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from langchain_core.embeddings import Embeddings

from app.api.routes import health_router, indexing_router, insights_router, query_router
from app.clients.github_client import GitHubServiceClient
from app.clients.identity_client import IdentityClient
from app.clients.project_client import ProjectClient
from app.core.config import Settings, load_settings
from app.core.database import build_async_engine, build_session_factory
from app.core.exceptions import register_exception_handlers
from app.core.logging import MetricsRegistry, configure_logging, get_logger
from app.core.security import CorrelationIDMiddleware
from app.services.embedding_service import EmbeddingService
from app.services.rag_service import LLMClient, MockLLMHandler
from app.services.vector_store_service import VectorStoreService


def create_app(
    *,
    settings: Settings | None = None,
    identity_transport: httpx.AsyncBaseTransport | None = None,
    project_transport: httpx.AsyncBaseTransport | None = None,
    github_transport: httpx.AsyncBaseTransport | None = None,
    embedding_transport: httpx.AsyncBaseTransport | None = None,
    llm_transport: httpx.AsyncBaseTransport | None = None,
    mock_embeddings: Embeddings | None = None,
    mock_llm_handler: MockLLMHandler | None = None,
) -> FastAPI:
    """Create and configure the independent Code Context Service FastAPI app."""
    cfg = settings or load_settings()
    configure_logging(level=cfg.LOG_LEVEL, service_name="code-context-service")
    logger = get_logger("main")

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        engine = build_async_engine(cfg)
        session_factory = build_session_factory(engine)
        metrics = MetricsRegistry()

        identity_client = IdentityClient(cfg, transport=identity_transport)
        project_client = ProjectClient(cfg, transport=project_transport)
        github_service_client = GitHubServiceClient(cfg, transport=github_transport)
        embedding_service = EmbeddingService(
            cfg,
            metrics=metrics,
            transport=embedding_transport,
            mock_adapter=mock_embeddings,
        )
        vector_store_service = VectorStoreService(cfg)
        llm_client = LLMClient(
            cfg,
            metrics=metrics,
            transport=llm_transport,
            mock_handler=mock_llm_handler,
        )

        application.state.settings = cfg
        application.state.engine = engine
        application.state.session_factory = session_factory
        application.state.metrics = metrics
        application.state.identity_client = identity_client
        application.state.project_client = project_client
        application.state.github_service_client = github_service_client
        application.state.embedding_service = embedding_service
        application.state.vector_store_service = vector_store_service
        application.state.llm_client = llm_client

        await identity_client.startup()
        await project_client.startup()
        await github_service_client.startup()
        await embedding_service.startup()
        await llm_client.startup()
        vector_store_service.startup()

        logger.info(
            "Code Context Service started",
            extra={
                "event": "service_startup",
            },
        )
        try:
            yield
        finally:
            vector_store_service.close()
            await llm_client.close()
            await embedding_service.close()
            await github_service_client.close()
            await project_client.close()
            await identity_client.close()
            await engine.dispose()
            logger.info(
                "Code Context Service shut down cleanly",
                extra={
                    "event": "service_shutdown",
                },
            )

    application = FastAPI(
        title=cfg.APP_NAME,
        version=cfg.APP_VERSION,
        description=(
            "CodeBridge Code Context Service — read-only repository ingestion via GitHub Service, "
            "LangChain semantic code chunking, ChromaDB retrieval, grounded RAG explanations, and "
            "deterministic + AI repository health insights."
        ),
        docs_url="/docs" if cfg.APP_ENV != "production" else None,
        redoc_url="/redoc" if cfg.APP_ENV != "production" else None,
        openapi_url="/openapi.json" if cfg.APP_ENV != "production" else None,
        lifespan=lifespan,
    )

    register_exception_handlers(application)
    application.add_middleware(CorrelationIDMiddleware)

    application.include_router(health_router)
    api_prefix = cfg.API_V1_PREFIX.rstrip("/")
    application.include_router(indexing_router, prefix=api_prefix)
    application.include_router(query_router, prefix=api_prefix)
    application.include_router(insights_router, prefix=api_prefix)

    return application


app = create_app()
