"""RAG grounding, citation validation, LLM fallback, & 503 failure behavior tests."""

from __future__ import annotations

import httpx
import pytest

from app.main import create_app
from tests.conftest import (
    OWNER_TOKEN,
    PROJECT_ID,
    CallableMockLLM,
    auth_headers,
    init_test_schema,
    make_github_service_transport,
    make_identity_transport,
    make_project_transport,
)


@pytest.mark.asyncio
async def test_rag_with_llm_none_uses_retrieval_fallback(base_test_settings) -> None:
    cfg = base_test_settings.model_copy(update={"LLM_PROVIDER": "none"})
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
            await client.post(
                f"/api/v1/code-context/projects/{PROJECT_ID}/index",
                headers=auth_headers(OWNER_TOKEN),
            )
            resp = await client.post(
                f"/api/v1/code-context/projects/{PROJECT_ID}/query",
                json={"query": "How does handle_payment_callback update invoice status?"},
                headers=auth_headers(OWNER_TOKEN),
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["ai_generated"] is False
            assert data["fallback_used"] is True
            assert data["grounded"] is True
            assert data["retrieval_count"] >= 1


@pytest.mark.asyncio
async def test_rag_rejects_hallucinated_unretrieved_file_citation(base_test_settings) -> None:
    cfg = base_test_settings.model_copy(update={"LLM_PROVIDER": "mock"})
    await init_test_schema(cfg)

    def hallucinating_llm(_sys: str, _user: str) -> str:
        return (
            "The logic is implemented in app/nonexistent/magic_handler.py lines 10-40 "
            "which is definitely the file you want."
        )

    app = create_app(
        settings=cfg,
        identity_transport=make_identity_transport(),
        project_transport=make_project_transport(),
        github_transport=make_github_service_transport(),
        mock_llm_handler=CallableMockLLM(hallucinating_llm),
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
            resp = await client.post(
                f"/api/v1/code-context/projects/{PROJECT_ID}/query",
                json={"query": "How does handle_payment_callback work?"},
                headers=auth_headers(OWNER_TOKEN),
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert "magic_handler.py" not in data["answer"]
            assert data["ai_generated"] is False
            assert data["fallback_used"] is True


@pytest.mark.asyncio
async def test_openai_llm_success_and_503_when_fallback_disabled(base_test_settings) -> None:
    cfg_ok = base_test_settings.model_copy(
        update={
            "LLM_PROVIDER": "openai",
            "LLM_API_KEY": "sk-test-fake-key",
            "LLM_FALLBACK_ON_ERROR": False,
        }
    )
    await init_test_schema(cfg_ok)

    def openai_ok(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "In `app/payments/webhook.py` (lines 1-7), `handle_payment_callback` marks status as PAID when `type == 'payment.succeeded'`."
                        }
                    }
                ]
            },
        )

    app_ok = create_app(
        settings=cfg_ok,
        identity_transport=make_identity_transport(),
        project_transport=make_project_transport(),
        github_transport=make_github_service_transport(),
        llm_transport=httpx.MockTransport(openai_ok),
    )
    async with app_ok.router.lifespan_context(app_ok):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_ok),
            base_url="http://code-context.test",
        ) as client:
            await client.post(
                f"/api/v1/code-context/projects/{PROJECT_ID}/index",
                headers=auth_headers(OWNER_TOKEN),
            )
            q_ok = await client.post(
                f"/api/v1/code-context/projects/{PROJECT_ID}/query",
                json={"query": "Explain handle_payment_callback"},
                headers=auth_headers(OWNER_TOKEN),
            )
            assert q_ok.status_code == 200
            d_ok = q_ok.json()["data"]
            assert d_ok["ai_generated"] is True
            assert d_ok["fallback_used"] is False
            assert "app/payments/webhook.py" in d_ok["answer"]

    # Now verify explicit 503 when LLM fails and LLM_FALLBACK_ON_ERROR=False
    app_fail = create_app(
        settings=cfg_ok,
        identity_transport=make_identity_transport(),
        project_transport=make_project_transport(),
        github_transport=make_github_service_transport(),
        llm_transport=httpx.MockTransport(lambda _r: httpx.Response(503, json={"err": "down"})),
    )
    async with app_fail.router.lifespan_context(app_fail):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_fail),
            base_url="http://code-context.test",
        ) as client:
            q_fail = await client.post(
                f"/api/v1/code-context/projects/{PROJECT_ID}/query",
                json={"query": "Explain handle_payment_callback"},
                headers=auth_headers(OWNER_TOKEN),
            )
            assert q_fail.status_code == 503
            assert q_fail.json()["error"]["code"] == "LLM_PROVIDER_UNAVAILABLE"

