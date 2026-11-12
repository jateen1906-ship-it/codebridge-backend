"""Tests for service routing, HTTP method/query/body forwarding, and header isolation."""

from __future__ import annotations

import json

import httpx
import pytest

from app.config import Settings
from app.main import create_app


@pytest.mark.asyncio
async def test_all_12_microservice_routes_forward_to_configured_urls() -> None:
    """All PRD public route groups forward to their designated microservice URLs."""
    seen_targets: list[tuple[str, str]] = []

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        netloc = request.url.netloc.decode("ascii")
        seen_targets.append((f"{request.url.scheme}://{netloc}", request.url.path))
        return httpx.Response(200, json={"path": request.url.path})

    app = create_app(Settings(_env_file=None), http_transport=httpx.MockTransport(mock_handler))

    route_expectations = [
        ("/api/v1/auth/login", "http://localhost:8001", "/api/v1/auth/login"),
        ("/api/v1/freelancers/12", "http://localhost:8001", "/api/v1/freelancers/12"),
        ("/api/v1/profile", "http://localhost:8001", "/api/v1/profile"),
        ("/api/v1/profile/verified-work", "http://localhost:8007", "/api/v1/verification/me"),
        ("/api/v1/projects/42/tasks", "http://localhost:8002", "/api/v1/projects/42/tasks"),
        ("/api/v1/projects/42/github/commits", "http://localhost:8003", "/api/v1/projects/42/github/commits"),
        ("/api/v1/github/oauth/callback", "http://localhost:8003", "/api/v1/github/oauth/callback"),
        ("/api/v1/code-context/analyze", "http://localhost:8004", "/api/v1/code-context/analyze"),
        ("/api/v1/workspace/ws-1/start", "http://localhost:8005", "/api/v1/workspace/ws-1/start"),
        ("/api/v1/submissions/sub-1", "http://localhost:8006", "/api/v1/submissions/sub-1"),
        ("/api/v1/review/sub-1/approve", "http://localhost:8006", "/api/v1/review/sub-1/approve"),
        ("/api/v1/verification/run", "http://localhost:8007", "/api/v1/verification/run"),
        ("/api/v1/payment/escrow", "http://localhost:8008", "/api/v1/payment/escrow"),
        ("/api/v1/notifications/unread", "http://localhost:8009", "/api/v1/notifications/unread"),
        ("/api/v1/admin/reports", "http://localhost:8010", "/api/v1/admin/reports"),
        ("/api/v1/audit/logs", "http://localhost:8011", "/api/v1/audit/logs"),
        ("/api/v1/observability/metrics", "http://localhost:8012", "/api/v1/observability/metrics"),
    ]

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            for public_path, _, _ in route_expectations:
                response = await client.get(public_path)
                assert response.status_code == 200

    expected_targets = [(base, path) for _, base, path in route_expectations]
    assert seen_targets == expected_targets


@pytest.mark.asyncio
async def test_github_project_integration_route_takes_precedence_over_projects() -> None:
    """Specific /projects/{project_id}/github routes match github-service before project-service."""
    routed_targets: list[str] = []

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        netloc = request.url.netloc.decode("ascii")
        routed_targets.append(f"{netloc}{request.url.path}")
        return httpx.Response(200, json={"host": netloc})

    app = create_app(Settings(_env_file=None), http_transport=httpx.MockTransport(mock_handler))

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            gh_root = await client.get("/api/v1/projects/proj-99/github")
            gh_sub = await client.post("/api/v1/projects/proj-99/github/sync", json={"branch": "main"})
            proj_sub = await client.get("/api/v1/projects/proj-99/requirements")

    assert gh_root.json()["host"] == "localhost:8003"
    assert gh_sub.json()["host"] == "localhost:8003"
    assert proj_sub.json()["host"] == "localhost:8002"
    assert routed_targets == [
        "localhost:8003/api/v1/projects/proj-99/github",
        "localhost:8003/api/v1/projects/proj-99/github/sync",
        "localhost:8002/api/v1/projects/proj-99/requirements",
    ]


@pytest.mark.asyncio
async def test_get_and_post_forwarding_with_query_params_and_body() -> None:
    """GET and POST requests preserve HTTP methods, query parameters, bodies, and status codes."""
    captured: list[dict] = []

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            {
                "method": request.method,
                "query": list(request.url.params.multi_items()),
                "body": request.content.decode("utf-8") if request.content else None,
                "content_type": request.headers.get("content-type"),
            }
        )
        if request.method == "POST":
            return httpx.Response(201, json={"id": "proj-1", "created": True})
        return httpx.Response(200, json={"items": [1, 2]})

    app = create_app(Settings(_env_file=None), http_transport=httpx.MockTransport(mock_handler))

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            get_resp = await client.get("/api/v1/projects?status=open&tag=python&tag=fastapi&page=2")
            post_resp = await client.post(
                "/api/v1/projects",
                json={"title": "Finish Auth Module", "budget": 500},
            )

    assert get_resp.status_code == 200
    assert get_resp.json() == {"items": [1, 2]}
    assert captured[0]["method"] == "GET"
    assert captured[0]["query"] == [
        ("status", "open"),
        ("tag", "python"),
        ("tag", "fastapi"),
        ("page", "2"),
    ]
    assert captured[0]["body"] is None

    assert post_resp.status_code == 201
    assert post_resp.json() == {"id": "proj-1", "created": True}
    assert captured[1]["method"] == "POST"
    assert json.loads(captured[1]["body"]) == {"title": "Finish Auth Module", "budget": 500}
    assert "application/json" in captured[1]["content_type"]


@pytest.mark.asyncio
async def test_strips_hop_by_hop_host_and_unsafe_headers_and_preserves_content_types() -> None:
    """Unsafe/hop-by-hop headers are stripped; non-JSON and 204 responses remain intact."""
    captured_requests: list[dict[str, str]] = []

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append({k.lower(): v for k, v in request.headers.items()})
        if request.url.path.endswith("/plain"):
            return httpx.Response(
                200,
                content=b"plain-text-report",
                headers={
                    "Content-Type": "text/plain; charset=utf-8",
                    "Connection": "keep-alive",
                    "Transfer-Encoding": "chunked",
                    "Server": "internal-uvicorn",
                },
            )
        return httpx.Response(204)

    app = create_app(Settings(_env_file=None), http_transport=httpx.MockTransport(mock_handler))

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            plain_resp = await client.get(
                "/api/v1/verification/plain",
                headers={
                    "Authorization": "Bearer test-token",
                    "Proxy-Authorization": "Basic secret",
                    "TE": "trailers",
                    "X-User-ID": "spoofed-admin",
                    "X-Internal-Service": "fake",
                    "X-Forwarded-Host": "evil.local",
                },
            )
            empty_resp = await client.delete("/api/v1/notifications/123")

    assert plain_resp.status_code == 200
    assert plain_resp.text == "plain-text-report"
    assert "text/plain" in plain_resp.headers["content-type"]
    assert "server" not in plain_resp.headers
    assert "transfer-encoding" not in plain_resp.headers

    plain_outbound = captured_requests[0]
    assert plain_outbound.get("authorization") == "Bearer test-token"
    assert plain_outbound.get("host") == "localhost:8007"
    assert "proxy-authorization" not in plain_outbound
    assert "te" not in plain_outbound
    assert "x-user-id" not in plain_outbound
    assert "x-internal-service" not in plain_outbound
    assert "x-forwarded-host" not in plain_outbound

    assert empty_resp.status_code == 204
    assert empty_resp.content == b""


@pytest.mark.asyncio
async def test_path_traversal_attempt_rejected_before_downstream_call() -> None:
    """Path traversal segments are rejected cleanly by the Gateway."""
    called = False

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    app = create_app(Settings(_env_file=None), http_transport=httpx.MockTransport(mock_handler))

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/api/v1/projects/%2e%2e/admin")

    assert not called
    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "VALIDATION_ERROR"
