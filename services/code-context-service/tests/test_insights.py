"""Repository deterministic health diagnostics & optional labeled AI insights tests."""

from __future__ import annotations

import json

import httpx
import pytest

from app.main import create_app
from tests.conftest import (
    OWNER_TOKEN,
    PROJECT_ID,
    auth_headers,
    init_test_schema,
    make_github_service_transport,
    make_identity_transport,
    make_project_transport,
)


@pytest.mark.asyncio
async def test_insights_require_index_and_persisted_insights(
    async_client: httpx.AsyncClient,
) -> None:
    r_before_gen = await async_client.post(
        f"/api/v1/code-context/projects/{PROJECT_ID}/insights/generate",
        headers=auth_headers(OWNER_TOKEN),
    )
    assert r_before_gen.status_code == 404
    assert r_before_gen.json()["error"]["code"] == "INDEX_NOT_FOUND"

    r_before_get = await async_client.get(
        f"/api/v1/code-context/projects/{PROJECT_ID}/insights",
        headers=auth_headers(OWNER_TOKEN),
    )
    assert r_before_get.status_code == 404


@pytest.mark.asyncio
async def test_deterministic_and_mock_ai_insights_generation_and_retrieval(
    async_client: httpx.AsyncClient,
) -> None:
    idx_resp = await async_client.post(
        f"/api/v1/code-context/projects/{PROJECT_ID}/index",
        headers=auth_headers(OWNER_TOKEN),
    )
    assert idx_resp.status_code == 201

    gen_resp = await async_client.post(
        f"/api/v1/code-context/projects/{PROJECT_ID}/insights/generate",
        headers=auth_headers(OWNER_TOKEN),
    )
    assert gen_resp.status_code == 201
    data = gen_resp.json()["data"]
    assert data["index_version"] == 1
    assert data["ai_generated"] is True
    assert data["llm_provider"] == "mock"

    det = data["deterministic_report"]
    assert det["repository_full_name"] == "owner/sample-payment-app"
    assert det["languages_detected"]["python"] >= 3
    assert det["languages_detected"]["typescript"] == 1
    assert det["has_readme"] is True
    assert det["has_tests"] is True
    assert "tests/test_webhook.py" in det["test_file_paths"]
    assert "does not execute tests" in det["test_execution_note"].lower()
    assert "requirements.txt" in det["dependency_manifests"]
    assert "Dockerfile" in det["container_and_ci_configs"]
    assert ".github/workflows/ci.yml" in det["container_and_ci_configs"]

    ai = data["ai_summary"]
    assert "app/main.py" in ai["likely_entry_points"]

    get_resp = await async_client.get(
        f"/api/v1/code-context/projects/{PROJECT_ID}/insights",
        headers=auth_headers(OWNER_TOKEN),
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["data"]["id"] == data["id"]


@pytest.mark.asyncio
async def test_insights_openai_filters_hallucinated_paths_and_fallback_works(
    base_test_settings,
) -> None:
    cfg = base_test_settings.model_copy(
        update={
            "LLM_PROVIDER": "openai",
            "LLM_API_KEY": "sk-test-fake",
        }
    )
    await init_test_schema(cfg)

    def openai_handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "architecture_overview": "FastAPI payment webhook service.",
                                    "likely_entry_points": [
                                        "app/main.py",
                                        "app/fake_hallucinated.py",
                                    ],
                                    "missing_setup_or_hygiene_signals": ["No linter config"],
                                    "high_risk_or_incomplete_areas": [],
                                    "evidence_files": ["app/payments/webhook.py", "nope.py"],
                                }
                            )
                        }
                    }
                ]
            },
        )

    app = create_app(
        settings=cfg,
        identity_transport=make_identity_transport(),
        project_transport=make_project_transport(),
        github_transport=make_github_service_transport(),
        llm_transport=httpx.MockTransport(openai_handler),
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
            gen = await client.post(
                f"/api/v1/code-context/projects/{PROJECT_ID}/insights/generate",
                headers=auth_headers(OWNER_TOKEN),
            )
            assert gen.status_code == 201
            ai = gen.json()["data"]["ai_summary"]
            assert ai["likely_entry_points"] == ["app/main.py"]
            assert ai["evidence_files"] == ["app/payments/webhook.py"]

