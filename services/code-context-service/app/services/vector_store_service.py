"""ChromaDB persistent/ephemeral vector store lifecycle, isolation, & similarity search."""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.core.exceptions import IndexingFailedError, UpstreamServiceUnavailableError
from app.core.logging import get_logger
from app.services.chunking_service import ChunkDraft

if TYPE_CHECKING:
    from app.core.config import Settings

logger = get_logger("services.vector_store")


@dataclass(frozen=True, slots=True)
class VectorSearchHit:
    """Single retrieved code chunk hit returned from ChromaDB."""

    vector_id: str
    content: str
    file_path: str
    language: str
    start_line: int
    end_line: int
    chunk_identifier: str
    relevance_score: float


def build_collection_name(prefix: str, project_id: uuid.UUID, index_version: int) -> str:
    """Construct a Chroma-compliant isolated collection name (<64 chars) per project & version."""
    safe_prefix = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in prefix).strip("_")
    if not safe_prefix or not safe_prefix[0].isalnum():
        safe_prefix = "cb"
    suffix = f"_{project_id.hex}_v{index_version}"
    max_prefix_len = max(2, 63 - len(suffix))
    return f"{safe_prefix[:max_prefix_len]}{suffix}"


def build_vector_id(repository_index_id: uuid.UUID, chunk_identifier: str) -> str:
    """Deterministic vector ID unique within a versioned Chroma collection."""
    raw = f"{repository_index_id.hex}:{chunk_identifier}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:40]


class VectorStoreService:
    """Project-scoped ChromaDB vector store manager supporting persistent & ephemeral modes."""

    def __init__(self, settings: Settings) -> None:
        self._mode = settings.CHROMA_MODE
        self._persist_directory = settings.CHROMA_PERSIST_DIRECTORY
        self._collection_prefix = settings.CHROMA_COLLECTION_PREFIX
        self._client: chromadb.ClientAPI | None = None

    @property
    def is_ready(self) -> bool:
        return self._client is not None

    def startup(self) -> None:
        """Initialize ChromaDB PersistentClient or EphemeralClient with telemetry disabled."""
        if self._client is not None:
            return
        chroma_settings = ChromaSettings(
            anonymized_telemetry=False,
            allow_reset=(self._mode == "ephemeral"),
        )
        if self._mode == "persistent":
            os.makedirs(self._persist_directory, exist_ok=True)
            self._client = chromadb.PersistentClient(
                path=self._persist_directory,
                settings=chroma_settings,
            )
        else:
            self._client = chromadb.EphemeralClient(settings=chroma_settings)

    def close(self) -> None:
        """Release references to the ChromaDB client cleanly on shutdown."""
        if self._client is not None and self._mode == "ephemeral":
            try:
                self._client.clear_system_cache()
            except Exception:
                pass
        self._client = None

    def _require_client(self) -> chromadb.ClientAPI:
        if self._client is None:
            raise UpstreamServiceUnavailableError(
                "Vector store is currently unavailable.",
                error_code="SERVICE_UNAVAILABLE",
            )
        return self._client

    def build_versioned_collection_name(self, project_id: uuid.UUID, index_version: int) -> str:
        return build_collection_name(self._collection_prefix, project_id, index_version)

    def _upsert_sync(
        self,
        *,
        collection_name: str,
        project_id: uuid.UUID,
        repository_index_id: uuid.UUID,
        index_version: int,
        chunks: list[ChunkDraft],
        embeddings: list[list[float]],
    ) -> list[str]:
        client = self._require_client()
        collection = client.get_or_create_collection(
            name=collection_name,
            metadata={
                "hnsw:space": "cosine",
                "project_id": str(project_id),
                "repository_index_id": str(repository_index_id),
                "index_version": int(index_version),
            },
        )
        vector_ids = [
            build_vector_id(repository_index_id, chunk.chunk_identifier) for chunk in chunks
        ]

        # Insert in modest batches so Chroma SQLite/HNSW transactions remain responsive.
        batch_size = 100
        for start in range(0, len(chunks), batch_size):
            end = start + batch_size
            batch_chunks = chunks[start:end]
            batch_vecs = embeddings[start:end]
            batch_ids = vector_ids[start:end]
            metadatas: list[dict[str, Any]] = [
                {
                    "project_id": str(project_id),
                    "repository_index_id": str(repository_index_id),
                    "index_version": int(index_version),
                    "file_path": chunk.file_path,
                    "language": chunk.language,
                    "start_line": int(chunk.start_line),
                    "end_line": int(chunk.end_line),
                    "chunk_index": int(chunk.chunk_index),
                    "chunk_identifier": chunk.chunk_identifier,
                    "content_hash": chunk.content_hash,
                }
                for chunk in batch_chunks
            ]
            collection.upsert(
                ids=batch_ids,
                embeddings=batch_vecs,  # type: ignore[arg-type]
                documents=[chunk.content for chunk in batch_chunks],
                metadatas=metadatas,  # type: ignore[arg-type]
            )

        stored_count = collection.count()
        if stored_count != len(chunks):
            raise IndexingFailedError(
                f"Vector store integrity check failed: expected {len(chunks)} vectors, got {stored_count}."
            )
        return vector_ids

    async def index_chunks(
        self,
        *,
        collection_name: str,
        project_id: uuid.UUID,
        repository_index_id: uuid.UUID,
        index_version: int,
        chunks: list[ChunkDraft],
        embeddings: list[list[float]],
    ) -> list[str]:
        """Upsert embedded code chunks into an isolated versioned ChromaDB collection."""
        if len(chunks) != len(embeddings):
            raise IndexingFailedError("Chunk and embedding count mismatch.")
        if not chunks:
            return []
        try:
            return await asyncio.to_thread(
                self._upsert_sync,
                collection_name=collection_name,
                project_id=project_id,
                repository_index_id=repository_index_id,
                index_version=index_version,
                chunks=chunks,
                embeddings=embeddings,
            )
        except IndexingFailedError:
            raise
        except Exception as exc:
            logger.warning(
                "ChromaDB indexing write failed",
                extra={
                    "event": "vector_store_write_error",
                    "project_id": str(project_id),
                    "index_version": index_version,
                    "error_code": "INDEXING_FAILED",
                },
            )
            raise IndexingFailedError("Failed to store repository vectors in ChromaDB.") from exc

    def _query_sync(
        self,
        *,
        collection_name: str,
        project_id: uuid.UUID,
        query_embedding: list[float],
        top_k: int,
    ) -> list[VectorSearchHit]:
        client = self._require_client()
        try:
            collection = client.get_collection(name=collection_name)
        except Exception:
            return []

        count = collection.count()
        if count <= 0:
            return []

        n_results = min(top_k, count)
        results = collection.query(
            query_embeddings=[query_embedding],  # type: ignore[arg-type]
            n_results=n_results,
            where={"project_id": str(project_id)},
            include=["documents", "metadatas", "distances"],
        )

        ids_row = (results.get("ids") or [[]])[0]
        docs_row = (results.get("documents") or [[]])[0]
        metas_row = (results.get("metadatas") or [[]])[0]
        dists_row = (results.get("distances") or [[]])[0]

        hits: list[VectorSearchHit] = []
        for idx, vec_id in enumerate(ids_row):
            meta = metas_row[idx] if idx < len(metas_row) and isinstance(metas_row[idx], dict) else {}
            if str(meta.get("project_id")) != str(project_id):
                continue
            doc_text = docs_row[idx] if idx < len(docs_row) and isinstance(docs_row[idx], str) else ""
            raw_dist = float(dists_row[idx]) if idx < len(dists_row) else 1.0
            score = round(max(0.0, min(1.0, 1.0 - raw_dist)), 4)
            hits.append(
                VectorSearchHit(
                    vector_id=str(vec_id),
                    content=doc_text,
                    file_path=str(meta.get("file_path", "")),
                    language=str(meta.get("language", "text")),
                    start_line=max(1, int(meta.get("start_line", 1))),
                    end_line=max(1, int(meta.get("end_line", 1))),
                    chunk_identifier=str(meta.get("chunk_identifier", vec_id)),
                    relevance_score=score,
                )
            )
        hits.sort(key=lambda item: item.relevance_score, reverse=True)
        return hits

    async def query_collection(
        self,
        *,
        collection_name: str,
        project_id: uuid.UUID,
        query_embedding: list[float],
        top_k: int,
    ) -> list[VectorSearchHit]:
        """Run a project-isolated similarity search on one active ChromaDB collection."""
        try:
            return await asyncio.to_thread(
                self._query_sync,
                collection_name=collection_name,
                project_id=project_id,
                query_embedding=query_embedding,
                top_k=top_k,
            )
        except Exception as exc:
            logger.warning(
                "ChromaDB similarity query failed",
                extra={
                    "event": "vector_store_query_error",
                    "project_id": str(project_id),
                    "error_code": "SERVICE_UNAVAILABLE",
                },
            )
            raise UpstreamServiceUnavailableError(
                "Vector search backend failed.",
                error_code="SERVICE_UNAVAILABLE",
            ) from exc

    def _delete_sync(self, collection_name: str) -> None:
        client = self._require_client()
        try:
            client.delete_collection(name=collection_name)
        except Exception:
            pass

    async def delete_collection(self, collection_name: str) -> None:
        """Safely remove a retired or aborted ChromaDB collection."""
        if self._client is None:
            return
        await asyncio.to_thread(self._delete_sync, collection_name)

