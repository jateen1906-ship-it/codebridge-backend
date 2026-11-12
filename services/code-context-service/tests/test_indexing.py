"""Repository indexing end-to-end tests: ingestion, limits, concurrency, & failure safety."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from app.main import create_app
from app.models.base import utc_now
from app.models.indexing_job import IndexingJob, IndexingJobStatus, IndexingJobType
from app.models.repository_index import IndexStatus, RepositoryIndex
from tests.conftest import (
    DISCONNECTED_PROJECT_ID,
    OWNER_ID,
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
async def test_initial_indexing_succeeds_and_status_reports_counts(
    async_client: httpx.AsyncClient,
) -> None:
    status_before = await async_client.get(
        f"/api/v1/code-context/projects/{PROJECT_ID}/index/status",
        headers=auth_headers(OWNER_TOKEN),
    )
    assert status_before.status_code == 404
    assert status_before.json()["error"]["code"] == "INDEX_NOT_FOUND"

    idx_resp = await async_client.post(
        f"/api/v1/code-context/projects/{PROJECT_ID}/index",
        json={"branch": "main"},
        headers=auth_headers(OWNER_TOKEN),
    )
    assert idx_resp.status_code == 201
    body = idx_resp.json()
    assert body["success"] is True
    assert body["index"]["index_version"] == 1
    assert body["index"]["is_active"] is True
    assert body["index"]["status"] == "READY"
    assert body["index"]["branch"] == "main"
    assert body["index"]["commit_sha"] == "commit-sha-main-001"
    assert body["index"]["files_indexed"] >= 6
    assert body["index"]["chunks_generated"] >= 6
    # .env (sensitive_file), assets/logo.png (binary_file), Dockerfile (unsupported_extension), app/__init__.py (empty_file) skipped
    assert body["skipped_reasons"]["sensitive_file"] >= 1
    assert body["skipped_reasons"]["binary_file"] >= 1

    st_resp = await async_client.get(
        f"/api/v1/code-context/projects/{PROJECT_ID}/index/status",
        headers=auth_headers(OWNER_TOKEN),
    )
    assert st_resp.status_code == 200
    st = st_resp.json()
    assert st["has_active_index"] is True
    assert st["index_status"] == "READY"
    assert st["indexed_languages"]["python"] >= 3
    assert st["indexed_languages"]["typescript"] == 1


@pytest.mark.asyncio
async def test_disconnected_repository_returns_404(async_client: httpx.AsyncClient) -> None:
    resp = await async_client.post(
        f"/api/v1/code-context/projects/{DISCONNECTED_PROJECT_ID}/index",
        headers=auth_headers(OWNER_TOKEN),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "REPOSITORY_NOT_CONNECTED"


@pytest.mark.asyncio
async def test_max_files_limit_exceeded_returns_422(base_test_settings) -> None:
    cfg = base_test_settings.model_copy(update={"MAX_FILES_PER_INDEX": 2})
    await init_test_schema(cfg)
    app = create_app(
        settings=cfg,
        identity_transport=make_identity_transport(),
        project_transport=make_project_transport(),
        github_transport=make_github_service_transport(),
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://code-context.test",
        ) as client:
            resp = await client.post(
                f"/api/v1/code-context/projects/{PROJECT_ID}/index",
                headers=auth_headers(OWNER_TOKEN),
            )
            assert resp.status_code == 422
            assert resp.json()["error"]["code"] == "INDEX_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_inflight_job_returns_409_indexing_in_progress(test_app) -> None:
    async with test_app.router.lifespan_context(test_app):
        async with test_app.state.session_factory() as session:
            idx = RepositoryIndex(
                project_id=PROJECT_ID,
                github_repository_id=987654,
                repository_full_name="owner/sample-payment-app",
                branch="main",
                index_version=1,
                collection_name=f"codebridge_{PROJECT_ID.hex}_v1",
                embedding_provider="mock",
                embedding_model="mock",
                status=IndexStatus.INDEXING,
                is_active=False,
                started_at=utc_now(),
            )
            session.add(idx)
            await session.flush()
            job = IndexingJob(
                project_id=PROJECT_ID,
                repository_index_id=idx.id,
                triggered_by=OWNER_ID,
                job_type=IndexingJobType.INITIAL,
                status=IndexingJobStatus.RUNNING,
                branch="main",
                started_at=utc_now(),
            )
            session.add(job)
            await session.commit()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=test_app),
            base_url="http://code-context.test",
        ) as client:
            resp = await client.post(
                f"/api/v1/code-context/projects/{PROJECT_ID}/index",
                headers=auth_headers(OWNER_TOKEN),
            )
            assert resp.status_code == 409
            assert resp.json()["error"]["code"] == "INDEXING_IN_PROGRESS"


@pytest.mark.asyncio
async def test_embedding_failure_marks_index_failed_without_leaving_active_corrupt(
    base_test_settings,
) -> None:
    cfg = base_test_settings.model_copy(
        update={
            "EMBEDDING_PROVIDER": "openai",
            "EMBEDDING_API_KEY": "sk-test-fake",
        }
    )
    await init_test_schema(cfg)
    app = create_app(
        settings=cfg,
        identity_transport=make_identity_transport(),
        project_transport=make_project_transport(),
        github_transport=make_github_service_transport(
            {
                str(PROJECT_ID): FakeGitHubRepoState(
                    files={"app/main.py": FakeRepoFile(content="def hello():\n    return 1\n")}
                )
            }
        ),
        embedding_transport=httpx.MockTransport(lambda _r: httpx.Response(503, json={"err": "down"})),
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://code-context.test",
        ) as client:
            resp = await client.post(
                f"/api/v1/code-context/projects/{PROJECT_ID}/index",
                headers=auth_headers(OWNER_TOKEN),
            )
            assert resp.status_code == 503
            assert resp.json()["error"]["code"] == "EMBEDDING_PROVIDER_UNAVAILABLE"

        async with app.state.session_factory() as session:
            row = (
                await session.execute(
                    select(RepositoryIndex).where(RepositoryIndex.project_id == PROJECT_ID)
                )
            ).scalar_one()
            assert row.status == IndexStatus.FAILED
            assert row.is_active is False

