"""Configurable LangChain-compatible embedding providers & batched vector generation."""

from __future__ import annotations

import hashlib
import math
import re
from typing import TYPE_CHECKING

import httpx
from langchain_core.embeddings import Embeddings

from app.core.exceptions import UpstreamServiceUnavailableError
from app.core.logging import MetricsRegistry, get_logger

if TYPE_CHECKING:
    from app.core.config import Settings

logger = get_logger("services.embedding")

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]{2,}")


def _normalize_vector(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm <= 1e-12:
        out = [0.0] * len(vec)
        if out:
            out[0] = 1.0
        return out
    return [round(v / norm, 6) for v in vec]


class DeterministicMockEmbeddings(Embeddings):
    """Deterministic LangChain Embeddings adapter for isolated offline tests & dev mode.

    Uses token & trigram feature hashing into a normalized dense vector so lexical
    and identifier matches produce meaningfully higher cosine similarity without any
    external network calls.
    """

    def __init__(self, dimension: int = 64) -> None:
        self.dimension = max(8, dimension)

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dimension
        lowered = text.lower()
        tokens = _TOKEN_RE.findall(lowered)
        if not tokens:
            tokens = [lowered[:32] or "empty"]

        for tok in tokens:
            digest = hashlib.sha256(tok.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % self.dimension
            sign = 1.0 if (digest[4] & 1) == 0 else -1.0
            vec[idx] += sign * 2.0

            # Also project character trigrams for partial-identifier similarity
            if len(tok) >= 4:
                for i in range(len(tok) - 2):
                    gram = tok[i : i + 3]
                    g_digest = hashlib.sha256(gram.encode("utf-8")).digest()
                    g_idx = int.from_bytes(g_digest[:4], "big") % self.dimension
                    g_sign = 1.0 if (g_digest[4] & 1) == 0 else -1.0
                    vec[g_idx] += g_sign * 0.5

        return _normalize_vector(vec)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed_one(text)


class EmbeddingService:
    """Configurable batched embedding generator supporting OpenAI, Ollama, and mock adapters."""

    def __init__(
        self,
        settings: Settings,
        metrics: MetricsRegistry | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        mock_adapter: Embeddings | None = None,
    ) -> None:
        self._provider = settings.EMBEDDING_PROVIDER
        self._model = settings.EMBEDDING_MODEL
        self._base_url = settings.EMBEDDING_API_BASE_URL.rstrip("/")
        self._api_key = settings.EMBEDDING_API_KEY
        self._timeout_seconds = settings.EMBEDDING_TIMEOUT_SECONDS
        self._batch_size = settings.EMBEDDING_BATCH_SIZE
        self._metrics = metrics
        self._transport = transport
        self._mock_adapter: Embeddings = mock_adapter or DeterministicMockEmbeddings(
            dimension=settings.EMBEDDING_DIMENSION
        )
        self._client: httpx.AsyncClient | None = None

    @property
    def provider_name(self) -> str:
        return self._provider

    @property
    def model_name(self) -> str:
        return self._model

    async def startup(self) -> None:
        if self._provider in {"openai", "ollama"} and (
            self._client is None or self._client.is_closed
        ):
            timeout = httpx.Timeout(
                timeout=self._timeout_seconds,
                connect=min(5.0, self._timeout_seconds),
            )
            client_kwargs: dict[str, object] = {
                "base_url": self._base_url,
                "timeout": timeout,
                "follow_redirects": False,
            }
            if self._transport is not None:
                client_kwargs["transport"] = self._transport
            self._client = httpx.AsyncClient(**client_kwargs)  # type: ignore[arg-type]

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    def _record_failure(self) -> None:
        if self._metrics is not None:
            self._metrics.increment("embedding_provider_failures")

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            raise UpstreamServiceUnavailableError(
                "Embedding provider client is unavailable.",
                error_code="EMBEDDING_PROVIDER_UNAVAILABLE",
            )
        return self._client

    async def _embed_openai_batch(self, batch: list[str]) -> list[list[float]]:
        if not self._api_key:
            self._record_failure()
            raise UpstreamServiceUnavailableError(
                "Embedding provider credentials are not configured.",
                error_code="EMBEDDING_PROVIDER_UNAVAILABLE",
            )
        client = self._require_client()
        try:
            response = await client.post(
                "/embeddings",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._model,
                    "input": batch,
                },
            )
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            self._record_failure()
            logger.warning(
                "OpenAI embedding request failed",
                extra={
                    "event": "embedding_provider_error",
                    "error_code": "EMBEDDING_PROVIDER_UNAVAILABLE",
                },
            )
            raise UpstreamServiceUnavailableError(
                "Embedding provider is temporarily unavailable.",
                error_code="EMBEDDING_PROVIDER_UNAVAILABLE",
            ) from exc

        if response.status_code != 200:
            self._record_failure()
            logger.warning(
                "OpenAI embedding provider returned non-200 status",
                extra={
                    "event": "embedding_provider_status",
                    "status_code": response.status_code,
                    "error_code": "EMBEDDING_PROVIDER_UNAVAILABLE",
                },
            )
            raise UpstreamServiceUnavailableError(
                "Embedding provider returned an error response.",
                error_code="EMBEDDING_PROVIDER_UNAVAILABLE",
            )

        try:
            payload = response.json()
            items = payload["data"]
            items_sorted = sorted(items, key=lambda x: int(x.get("index", 0)))
            vectors = [[float(v) for v in item["embedding"]] for item in items_sorted]
        except Exception as exc:
            self._record_failure()
            raise UpstreamServiceUnavailableError(
                "Embedding provider returned an invalid response payload.",
                error_code="EMBEDDING_PROVIDER_UNAVAILABLE",
            ) from exc

        if len(vectors) != len(batch):
            self._record_failure()
            raise UpstreamServiceUnavailableError(
                "Embedding provider returned mismatched embedding count.",
                error_code="EMBEDDING_PROVIDER_UNAVAILABLE",
            )
        return vectors

    async def _embed_ollama_batch(self, batch: list[str]) -> list[list[float]]:
        client = self._require_client()
        try:
            response = await client.post(
                "/api/embed",
                headers={"Content-Type": "application/json"},
                json={"model": self._model, "input": batch},
            )
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            self._record_failure()
            raise UpstreamServiceUnavailableError(
                "Embedding provider is temporarily unavailable.",
                error_code="EMBEDDING_PROVIDER_UNAVAILABLE",
            ) from exc

        if response.status_code != 200:
            self._record_failure()
            raise UpstreamServiceUnavailableError(
                "Embedding provider returned an error response.",
                error_code="EMBEDDING_PROVIDER_UNAVAILABLE",
            )

        try:
            payload = response.json()
            vectors = [[float(v) for v in emb] for emb in payload["embeddings"]]
        except Exception as exc:
            self._record_failure()
            raise UpstreamServiceUnavailableError(
                "Embedding provider returned an invalid response payload.",
                error_code="EMBEDDING_PROVIDER_UNAVAILABLE",
            ) from exc

        if len(vectors) != len(batch):
            self._record_failure()
            raise UpstreamServiceUnavailableError(
                "Embedding provider returned mismatched embedding count.",
                error_code="EMBEDDING_PROVIDER_UNAVAILABLE",
            )
        return vectors

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Generate dense embedding vectors for document chunks in configurable batches."""
        if not texts:
            return []

        if self._provider == "mock":
            return self._mock_adapter.embed_documents(texts)

        all_vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            if self._provider == "openai":
                all_vectors.extend(await self._embed_openai_batch(batch))
            elif self._provider == "ollama":
                all_vectors.extend(await self._embed_ollama_batch(batch))
            else:
                self._record_failure()
                raise UpstreamServiceUnavailableError(
                    "Unsupported embedding provider configured.",
                    error_code="EMBEDDING_PROVIDER_UNAVAILABLE",
                )
        return all_vectors

    async def embed_query(self, query_text: str) -> list[float]:
        """Generate a single query embedding vector."""
        if self._provider == "mock":
            return self._mock_adapter.embed_query(query_text)
        vectors = await self.embed_documents([query_text])
        if not vectors:
            self._record_failure()
            raise UpstreamServiceUnavailableError(
                "Embedding provider returned an empty query embedding.",
                error_code="EMBEDDING_PROVIDER_UNAVAILABLE",
            )
        return vectors[0]

