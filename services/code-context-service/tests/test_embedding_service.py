"""Embedding adapter tests: DeterministicMockEmbeddings, OpenAI, Ollama, & error mapping."""

from __future__ import annotations

import math

import httpx
import pytest

from app.core.exceptions import UpstreamServiceUnavailableError
from app.core.logging import MetricsRegistry
from app.services.embedding_service import DeterministicMockEmbeddings, EmbeddingService


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


@pytest.mark.asyncio
async def test_mock_embeddings_deterministic_and_lexically_meaningful(base_test_settings) -> None:
    metrics = MetricsRegistry()
    svc = EmbeddingService(base_test_settings, metrics=metrics)
    await svc.startup()
    try:
        docs = [
            "def handle_payment_callback(event): invoice status PAID",
            "html css navbar margin padding stylesheet color",
        ]
        vecs_a = await svc.embed_documents(docs)
        vecs_b = await svc.embed_documents(docs)
        assert vecs_a == vecs_b
        assert len(vecs_a) == 2
        assert isinstance(DeterministicMockEmbeddings(32).embed_query("test"), list)

        q_vec = await svc.embed_query("payment callback invoice status")
        assert math.isclose(sum(v * v for v in q_vec), 1.0, rel_tol=1e-3)
        assert _cosine(q_vec, vecs_a[0]) > _cosine(q_vec, vecs_a[1])
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_openai_embedding_batching_and_503_failure_tracking(base_test_settings) -> None:
    cfg = base_test_settings.model_copy(
        update={
            "EMBEDDING_PROVIDER": "openai",
            "EMBEDDING_API_KEY": "sk-test-fake-key-value",
            "EMBEDDING_BATCH_SIZE": 2,
        }
    )
    seen_batches: list[list[str]] = []

    def ok_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/embeddings")
        payload = request.read().decode("utf-8")
        import json

        parsed = json.loads(payload)
        batch = parsed["input"]
        seen_batches.append(batch)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": idx, "embedding": [float(idx + 1), 0.5, 0.25, 0.1]}
                    for idx in range(len(batch))
                ]
            },
        )

    metrics = MetricsRegistry()
    svc = EmbeddingService(cfg, metrics=metrics, transport=httpx.MockTransport(ok_handler))
    await svc.startup()
    try:
        vectors = await svc.embed_documents(["a", "b", "c"])
        assert len(vectors) == 3
        assert len(seen_batches) == 2
        assert seen_batches == [["a", "b"], ["c"]]
    finally:
        await svc.close()

    fail_svc = EmbeddingService(
        cfg,
        metrics=metrics,
        transport=httpx.MockTransport(lambda _r: httpx.Response(429, json={"error": "rate"})),
    )
    await fail_svc.startup()
    try:
        with pytest.raises(UpstreamServiceUnavailableError) as exc:
            await fail_svc.embed_documents(["test"])
        assert exc.value.error_code == "EMBEDDING_PROVIDER_UNAVAILABLE"
        assert metrics.snapshot()["embedding_provider_failures"] == 1
    finally:
        await fail_svc.close()


@pytest.mark.asyncio
async def test_ollama_embedding_adapter(base_test_settings) -> None:
    cfg = base_test_settings.model_copy(
        update={
            "EMBEDDING_PROVIDER": "ollama",
            "EMBEDDING_MODEL": "nomic-embed-text",
            "EMBEDDING_API_BASE_URL": "http://localhost:11434",
        }
    )
    svc = EmbeddingService(
        cfg,
        transport=httpx.MockTransport(
            lambda _r: httpx.Response(200, json={"embeddings": [[0.2, 0.8, 0.1, 0.4]]})
        ),
    )
    await svc.startup()
    try:
        q = await svc.embed_query("router")
        assert q == [0.2, 0.8, 0.1, 0.4]
    finally:
        await svc.close()

