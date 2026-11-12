"""Authorized, project-isolated ChromaDB similarity retrieval service."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.project_client import ProjectClient, assert_project_access
from app.core.dependencies import AuthContext
from app.core.exceptions import NotFoundError
from app.core.logging import MetricsRegistry, get_logger
from app.models.repository_index import RepositoryIndex
from app.repositories.index_repository import RepositoryIndexRepository
from app.services.embedding_service import EmbeddingService
from app.services.vector_store_service import VectorSearchHit, VectorStoreService

if TYPE_CHECKING:
    from app.core.config import Settings

logger = get_logger("services.retrieval")


def _cap_snippet_utf8(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    suffix = "\n... [truncated]"
    suffix_bytes = len(suffix.encode("utf-8"))
    if max_bytes <= suffix_bytes:
        return encoded[:max_bytes].decode("utf-8", errors="ignore").rstrip()
    truncated = encoded[: max_bytes - suffix_bytes].decode("utf-8", errors="ignore").rstrip()
    return f"{truncated}{suffix}"


@dataclass(frozen=True, slots=True)
class RetrievalBundle:
    """Bounded retrieved source evidence anchored to an active RepositoryIndex."""

    active_index: RepositoryIndex
    hits: list[VectorSearchHit]
    effective_top_k: int


class RetrievalService:
    """Executes authorized, strictly project-isolated semantic chunk retrieval."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        project_client: ProjectClient,
        embedding_service: EmbeddingService,
        vector_store_service: VectorStoreService,
        metrics: MetricsRegistry,
    ) -> None:
        self._settings = settings
        self._project_client = project_client
        self._embedding_service = embedding_service
        self._vector_store = vector_store_service
        self._metrics = metrics
        self._index_repo = RepositoryIndexRepository(session)

    async def retrieve_for_query(
        self,
        *,
        project_id: uuid.UUID,
        query: str,
        top_k: int | None,
        auth_ctx: AuthContext,
        request_id: str,
    ) -> RetrievalBundle:
        """Verify caller project permissions and fetch top-k relevant code chunks."""
        project = await self._project_client.get_project(
            project_id,
            bearer_token=auth_ctx.bearer_token,
            request_id=request_id,
        )
        assert_project_access(
            project,
            auth_ctx.user,
            allow_assigned_freelancer=True,
            allow_admin=False,
        )

        active_index = await self._index_repo.get_active_for_project(project_id)
        if active_index is None:
            raise NotFoundError(
                "No active repository index exists for this project. Trigger indexing first.",
                error_code="INDEX_NOT_FOUND",
            )

        requested_k = top_k if top_k is not None else self._settings.TOP_K_DEFAULT
        effective_k = max(1, min(requested_k, self._settings.TOP_K_MAX))

        query_vector = await self._embedding_service.embed_query(query)
        raw_hits = await self._vector_store.query_collection(
            collection_name=active_index.collection_name,
            project_id=project_id,
            query_embedding=query_vector,
            top_k=effective_k,
        )

        # Cap per-snippet and cumulative snippet payload sizes for safe bounded responses.
        max_total_bytes = self._settings.MAX_CONTENT_RESPONSE_BYTES
        per_snippet_cap = max(512, max_total_bytes // max(1, min(effective_k, 4)))
        remaining_bytes = max_total_bytes
        bounded_hits: list[VectorSearchHit] = []

        for hit in raw_hits:
            if remaining_bytes <= 128:
                break
            allowance = min(per_snippet_cap, remaining_bytes)
            capped_text = _cap_snippet_utf8(hit.content, allowance)
            remaining_bytes -= len(capped_text.encode("utf-8"))
            bounded_hits.append(
                VectorSearchHit(
                    vector_id=hit.vector_id,
                    content=capped_text,
                    file_path=hit.file_path,
                    language=hit.language,
                    start_line=hit.start_line,
                    end_line=hit.end_line,
                    chunk_identifier=hit.chunk_identifier,
                    relevance_score=hit.relevance_score,
                )
            )

        self._metrics.increment("queries_executed")
        self._metrics.increment("chunks_retrieved_total", len(bounded_hits))

        logger.info(
            "Code Context similarity retrieval executed",
            extra={
                "event": "code_context_retrieved",
                "project_id": str(project_id),
                "index_version": active_index.index_version,
                "retrieval_count": len(bounded_hits),
            },
        )
        return RetrievalBundle(
            active_index=active_index,
            hits=bounded_hits,
            effective_top_k=effective_k,
        )
