"""Reindexing tests: atomic version switch, failure preservation, & Chroma retention cleanup."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from app.main import create_app
from app.models.repository_index import IndexStatus, RepositoryIndex
from tests.conftest import (
    OWNER_TOKEN,
    PROJECT_ID,
    FakeGitHubRepoState,
    FakeRepoFile,
    auth_headers,
    build_sample_repo_files,
    init_test_schema,
    make_github_service_transport,
    make_identity_transport,
    make_project_transport,
)


@pytest.mark.asyncio
async def test_reindex_promotes_new_version_and_retires_old_vectors_past_retention(
    base_test_settings,
) -> None:
    cfg = base_test_settings.model_copy(update={"INDEX_RETENTION_VERSIONS": 2})
    await init_test_schema(cfg)
    repo_state = FakeGitHubRepoState(files=build_sample_repo_files())
    app = create_app(
        settings=cfg,
        identity_transport=make_identity_transport(),
        project_transport=make_project_transport(),
        github_transport=make_github_service_transport({str(PROJECT_ID): repo_state}),
    )

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://code-context.test",
        ) as client:
            r1 = await client.post(
                f"/api/v1/code-context/projects/{PROJECT_ID}/index",
                headers=auth_headers(OWNER_TOKEN),
            )
            assert r1.status_code == 201
            assert r1.json()["index"]["index_version"] == 1

            # Update repository with new function and reindex twice (v2, v3)
            repo_state.files["app/payments/refund.py"] = FakeRepoFile(
                content="def process_stripe_refund(charge_id: str) -> str:\n    return f'refunded:{charge_id}'\n",
                sha="sha-refund-v2",
            )
            r2 = await client.post(
                f"/api/v1/code-context/projects/{PROJECT_ID}/reindex",
                headers=auth_headers(OWNER_TOKEN),
            )
            assert r2.status_code == 200
            assert r2.json()["index"]["index_version"] == 2
            assert r2.json()["job"]["job_type"] == "REINDEX"

            r3 = await client.post(
                f"/api/v1/code-context/projects/{PROJECT_ID}/reindex",
                headers=auth_headers(OWNER_TOKEN),
            )
            assert r3.status_code == 200
            assert r3.json()["index"]["index_version"] == 3

            # Query must find v3 data cleanly and report index_version=3
            q_resp = await client.post(
                f"/api/v1/code-context/projects/{PROJECT_ID}/query",
                json={"query": "process_stripe_refund"},
                headers=auth_headers(OWNER_TOKEN),
            )
            assert q_resp.status_code == 200
            assert q_resp.json()["data"]["index_version"] == 3
            assert any(
                s["file_path"] == "app/payments/refund.py"
                for s in q_resp.json()["data"]["sources"]
            )

        # Verify v1 Chroma collection was pruned beyond retention=2, while DB rows remain intact.
        store = app.state.vector_store_service
        assert await store.query_collection(
            collection_name=store.build_versioned_collection_name(PROJECT_ID, 1),
            project_id=PROJECT_ID,
            query_embedding=[0.1] * cfg.EMBEDDING_DIMENSION,
            top_k=1,
        ) == []

        async with app.state.session_factory() as session:
            rows = (
                await session.execute(
                    select(RepositoryIndex)
                    .where(RepositoryIndex.project_id == PROJECT_ID)
                    .order_by(RepositoryIndex.index_version.asc())
                )
            ).scalars().all()
            assert [r.index_version for r in rows] == [1, 2, 3]
            assert [r.is_active for r in rows] == [False, False, True]
            assert rows[0].status == IndexStatus.STALE
            assert rows[1].status == IndexStatus.STALE
            assert rows[2].status == IndexStatus.READY


@pytest.mark.asyncio
async def test_failed_reindex_preserves_previous_active_index(base_test_settings) -> None:
    await init_test_schema(base_test_settings)
    repo_state = FakeGitHubRepoState(files=build_sample_repo_files())
    app = create_app(
        settings=base_test_settings,
        identity_transport=make_identity_transport(),
        project_transport=make_project_transport(),
        github_transport=make_github_service_transport({str(PROJECT_ID): repo_state}),
    )

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://code-context.test",
        ) as client:
            r1 = await client.post(
                f"/api/v1/code-context/projects/{PROJECT_ID}/index",
                headers=auth_headers(OWNER_TOKEN),
            )
            assert r1.status_code == 201

            # Trigger transient rate limit during reindex v2
            repo_state.rate_limit_contents = True
            r2 = await client.post(
                f"/api/v1/code-context/projects/{PROJECT_ID}/reindex",
                headers=auth_headers(OWNER_TOKEN),
            )
            assert r2.status_code == 429

            # v1 MUST remain active and queryable!
            q_resp = await client.post(
                f"/api/v1/code-context/projects/{PROJECT_ID}/query",
                json={"query": "handle_payment_callback"},
                headers=auth_headers(OWNER_TOKEN),
            )
            assert q_resp.status_code == 200
            assert q_resp.json()["data"]["index_version"] == 1
            assert q_resp.json()["data"]["retrieval_count"] >= 1

