"""ChromaDB persistent & ephemeral vector store isolation and lifecycle tests."""

from __future__ import annotations

import uuid

import pytest

from app.services.chunking_service import ChunkingService
from app.services.embedding_service import EmbeddingService
from app.services.vector_store_service import VectorStoreService


@pytest.mark.asyncio
async def test_chroma_project_isolation_and_versioned_collection_lifecycle(
    base_test_settings,
) -> None:
    chunker = ChunkingService(base_test_settings)
    emb = EmbeddingService(base_test_settings)
    store = VectorStoreService(base_test_settings)
    await emb.startup()
    store.startup()

    try:
        proj_a = uuid.uuid4()
        proj_b = uuid.uuid4()
        idx_a = uuid.uuid4()
        idx_b = uuid.uuid4()

        chunks_a = chunker.chunk_file(
            file_path="app/a.py",
            language="python",
            text="def stripe_invoice_settlement():\n    return 'PROJECT_ALPHA_SECRET'\n",
        )
        chunks_b = chunker.chunk_file(
            file_path="app/b.py",
            language="python",
            text="def stripe_invoice_settlement():\n    return 'PROJECT_BETA_ONLY'\n",
        )
        vecs_a = await emb.embed_documents([c.content for c in chunks_a])
        vecs_b = await emb.embed_documents([c.content for c in chunks_b])

        col_a = store.build_versioned_collection_name(proj_a, 1)
        col_b = store.build_versioned_collection_name(proj_b, 1)
        assert col_a != col_b
        assert len(col_a) <= 63

        await store.index_chunks(
            collection_name=col_a,
            project_id=proj_a,
            repository_index_id=idx_a,
            index_version=1,
            chunks=chunks_a,
            embeddings=vecs_a,
        )
        await store.index_chunks(
            collection_name=col_b,
            project_id=proj_b,
            repository_index_id=idx_b,
            index_version=1,
            chunks=chunks_b,
            embeddings=vecs_b,
        )

        q_vec = await emb.embed_query("stripe_invoice_settlement")
        hits_a = await store.query_collection(
            collection_name=col_a,
            project_id=proj_a,
            query_embedding=q_vec,
            top_k=5,
        )
        assert len(hits_a) == 1
        assert "PROJECT_ALPHA_SECRET" in hits_a[0].content
        assert "PROJECT_BETA_ONLY" not in hits_a[0].content

        # Even querying col_a with project_b ID returns 0 hits due to metadata where-filter.
        cross_hits = await store.query_collection(
            collection_name=col_a,
            project_id=proj_b,
            query_embedding=q_vec,
            top_k=5,
        )
        assert cross_hits == []

        await store.delete_collection(col_a)
        after_delete = await store.query_collection(
            collection_name=col_a,
            project_id=proj_a,
            query_embedding=q_vec,
            top_k=5,
        )
        assert after_delete == []
    finally:
        store.close()
        await emb.close()


def test_chroma_ephemeral_mode_initializes_cleanly(base_test_settings) -> None:
    cfg = base_test_settings.model_copy(update={"CHROMA_MODE": "ephemeral"})
    store = VectorStoreService(cfg)
    store.startup()
    assert store.is_ready is True
    store.close()
    assert store.is_ready is False

