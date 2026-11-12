"""Semantic retrieval ranking, top_k bounding, snippet size caps, & validation tests."""

from __future__ import annotations

import httpx
import pytest

from app.main import create_app
from tests.conftest import (
    OWNER_TOKEN,
    PROJECT_ID,
    FakeGitHubRepoState,
    FakeRepoFile,
    auth_headers,
    init_test_schema,
    make_github_service_transport,
    make_identity_transport,
    make_project_transport,
)


@pytest.mark.asyncio
async def test_query_before_indexing_returns_404_index_not_found(
    async_client: httpx.AsyncClient,
) -> None:
    resp = await async_client.post(
        f"/api/v1/code-context/projects/{PROJECT_ID}/query",
        json={"query": "Where is payment handled?"},
        headers=auth_headers(OWNER_TOKEN),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "INDEX_NOT_FOUND"


@pytest.mark.asyncio
async def test_query_validation_and_top_k_and_snippet_size_bounds(base_test_settings) -> None:
    cfg = base_test_settings.model_copy(
        update={
            "TOP_K_MAX": 3,
            "MAX_CONTENT_RESPONSE_BYTES": 1024,
        }
    )
    await init_test_schema(cfg)
    files = {
        f"app/module_{i}.py": FakeRepoFile(
            content=f"def payment_worker_{i}():\n    return 'stripe webhook payload {'x' * 500}'\n"
        )
        for i in range(6)
    }
    app = create_app(
        settings=cfg,
        identity_transport=make_identity_transport(),
        project_transport=make_project_transport(),
        github_transport=make_github_service_transport(
            {str(PROJECT_ID): FakeGitHubRepoState(files=files)}
        ),
    )

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://code-context.test",
        ) as client:
            idx = await client.post(
                f"/api/v1/code-context/projects/{PROJECT_ID}/index",
                headers=auth_headers(OWNER_TOKEN),
            )
            assert idx.status_code == 201

            bad_empty = await client.post(
                f"/api/v1/code-context/projects/{PROJECT_ID}/query",
                json={"query": "   "},
                headers=auth_headers(OWNER_TOKEN),
            )
            assert bad_empty.status_code == 422

            q = await client.post(
                f"/api/v1/code-context/projects/{PROJECT_ID}/query",
                json={"query": "stripe webhook payload", "top_k": 10},
                headers=auth_headers(OWNER_TOKEN),
            )
            assert q.status_code == 200
            data = q.json()["data"]
            assert data["retrieval_count"] <= 3
            total_snippet_bytes = sum(len(s["snippet"].encode("utf-8")) for s in data["sources"])
            assert total_snippet_bytes <= 1024


@pytest.mark.asyncio
async def test_zero_chunks_repository_query_returns_no_evidence_gracefully(
    base_test_settings,
) -> None:
    await init_test_schema(base_test_settings)
    app = create_app(
        settings=base_test_settings,
        identity_transport=make_identity_transport(),
        project_transport=make_project_transport(),
        github_transport=make_github_service_transport(
            {str(PROJECT_ID): FakeGitHubRepoState(files={"bin/data.png": FakeRepoFile(is_binary=True)})}
        ),
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://code-context.test",
        ) as client:
            await client.post(
                f"/api/v1/code-context/projects/{PROJECT_ID}/index",
                headers=auth_headers(OWNER_TOKEN),
            )
            q = await client.post(
                f"/api/v1/code-context/projects/{PROJECT_ID}/query",
                json={"query": "Where is auth implemented?"},
                headers=auth_headers(OWNER_TOKEN),
            )
            assert q.status_code == 200
            data = q.json()["data"]
            assert data["retrieval_count"] == 0
            assert data["sources"] == []
            assert data["ai_generated"] is False
            assert "No relevant code evidence" in data["answer"]

