"""Shared fixtures, fake microservice transports, and isolated DB/Chroma helpers."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import text

from app.core.config import Settings
from app.core.database import build_async_engine
from app.models.base import Base
from app.services.rag_service import MockLLMHandler

OWNER_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER_OWNER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
ASSIGNED_FREELANCER_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
UNASSIGNED_FREELANCER_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")
ADMIN_ID = uuid.UUID("55555555-5555-5555-5555-555555555555")
INACTIVE_USER_ID = uuid.UUID("66666666-6666-6666-6666-666666666666")

PROJECT_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
OTHER_PROJECT_ID = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
MISSING_PROJECT_ID = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
DISCONNECTED_PROJECT_ID = uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")

OWNER_TOKEN = "token-owner-valid"
OTHER_OWNER_TOKEN = "token-other-owner-valid"
ASSIGNED_FREELANCER_TOKEN = "token-freelancer-assigned"
UNASSIGNED_FREELANCER_TOKEN = "token-freelancer-unassigned"
ADMIN_TOKEN = "token-admin-valid"
INACTIVE_TOKEN = "token-inactive-user"

_USERS_BY_TOKEN: dict[str, dict[str, Any]] = {
    OWNER_TOKEN: {
        "id": str(OWNER_ID),
        "full_name": "Project Owner",
        "email": "owner@codebridge.dev",
        "role": "OWNER",
        "is_active": True,
        "is_verified": True,
    },
    OTHER_OWNER_TOKEN: {
        "id": str(OTHER_OWNER_ID),
        "full_name": "Other Owner",
        "email": "other@codebridge.dev",
        "role": "OWNER",
        "is_active": True,
        "is_verified": True,
    },
    ASSIGNED_FREELANCER_TOKEN: {
        "id": str(ASSIGNED_FREELANCER_ID),
        "full_name": "Assigned Dev",
        "email": "assigned@codebridge.dev",
        "role": "FREELANCER",
        "is_active": True,
        "is_verified": True,
    },
    UNASSIGNED_FREELANCER_TOKEN: {
        "id": str(UNASSIGNED_FREELANCER_ID),
        "full_name": "Unassigned Dev",
        "email": "unassigned@codebridge.dev",
        "role": "FREELANCER",
        "is_active": True,
        "is_verified": True,
    },
    ADMIN_TOKEN: {
        "id": str(ADMIN_ID),
        "full_name": "Platform Admin",
        "email": "admin@codebridge.dev",
        "role": "ADMIN",
        "is_active": True,
        "is_verified": True,
    },
    INACTIVE_TOKEN: {
        "id": str(INACTIVE_USER_ID),
        "full_name": "Inactive User",
        "email": "inactive@codebridge.dev",
        "role": "OWNER",
        "is_active": False,
        "is_verified": True,
    },
}


@dataclass
class FakeRepoFile:
    content: str = ""
    size: int | None = None
    sha: str = "sha-file-001"
    encoding: str = "utf-8"
    is_binary: bool = False
    truncated: bool = False


@dataclass
class FakeGitHubRepoState:
    full_name: str = "owner/sample-payment-app"
    github_repository_id: int = 987654
    default_branch: str = "main"
    branches: dict[str, str] = field(
        default_factory=lambda: {"main": "commit-sha-main-001", "develop": "commit-sha-dev-002"}
    )
    files: dict[str, FakeRepoFile] = field(default_factory=dict)
    connected: bool = True
    rate_limit_contents: bool = False
    contents_calls: list[tuple[str, str | None]] = field(default_factory=list)


def build_sample_repo_files() -> dict[str, FakeRepoFile]:
    return {
        "README.md": FakeRepoFile(
            content=(
                "# Sample Payment App\n\n"
                "FastAPI backend handling Stripe webhook callbacks and invoice state updates.\n"
            ),
            sha="sha-readme",
        ),
        "requirements.txt": FakeRepoFile(
            content="fastapi==0.115.0\nsqlalchemy==2.0.35\npytest==8.3.0\n",
            sha="sha-reqs",
        ),
        "Dockerfile": FakeRepoFile(
            content="FROM python:3.13-slim\nCMD [\"uvicorn\", \"app.main:app\"]\n",
            sha="sha-docker",
        ),
        ".github/workflows/ci.yml": FakeRepoFile(
            content="name: CI\non: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n",
            sha="sha-ci",
        ),
        "app/__init__.py": FakeRepoFile(content="", sha="sha-init"),
        "app/main.py": FakeRepoFile(
            content=(
                "from fastapi import FastAPI\n"
                "from app.payments.webhook import handle_payment_callback\n\n"
                "app = FastAPI(title='Payment Service')\n\n"
                "@app.post('/webhooks/stripe')\n"
                "async def stripe_webhook(payload: dict) -> dict:\n"
                "    return handle_payment_callback(payload)\n"
            ),
            sha="sha-main",
        ),
        "app/payments/webhook.py": FakeRepoFile(
            content=(
                "def handle_payment_callback(event: dict) -> dict:\n"
                "    \"\"\"Persist final payment status when invoice checkout completes.\"\"\"\n"
                "    event_type = event.get('type')\n"
                "    invoice_id = event.get('invoice_id')\n"
                "    if event_type == 'payment.succeeded':\n"
                "        return {'invoice_id': invoice_id, 'status': 'PAID'}\n"
                "    return {'invoice_id': invoice_id, 'status': 'PENDING'}\n"
            ),
            sha="sha-webhook",
        ),
        "frontend/src/checkout.ts": FakeRepoFile(
            content=(
                "export async function submitCheckout(invoiceId: string): Promise<Response> {\n"
                "  return fetch(`/api/invoices/${invoiceId}/pay`, { method: 'POST' });\n"
                "}\n"
            ),
            sha="sha-checkout",
        ),
        "tests/test_webhook.py": FakeRepoFile(
            content=(
                "from app.payments.webhook import handle_payment_callback\n\n"
                "def test_payment_succeeded():\n"
                "    result = handle_payment_callback({'type': 'payment.succeeded', 'invoice_id': 'inv_1'})\n"
                "    assert result['status'] == 'PAID'\n"
            ),
            sha="sha-test-webhook",
        ),
        ".env": FakeRepoFile(
            content="DATABASE_URL=postgres://prod:supersecret@db:5432/app\n",
            sha="sha-env",
        ),
        ".env.example": FakeRepoFile(
            content="DATABASE_URL=postgresql+asyncpg://postgres:change_me@localhost:5432/app\n",
            sha="sha-env-example",
        ),
        "node_modules/pkg/index.js": FakeRepoFile(
            content="console.log('should never be indexed');\n",
            sha="sha-node-mod",
        ),
        "assets/logo.png": FakeRepoFile(
            content="iVBORw0KGgoAAAANSUhEUg==",
            encoding="base64",
            is_binary=True,
            size=1024,
            sha="sha-logo",
        ),
    }


def make_identity_transport(
    *,
    force_status: int | None = None,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if force_status is not None:
            return httpx.Response(force_status, json={"detail": "Forced identity error"})
        if request.url.path != "/api/v1/auth/me":
            return httpx.Response(404, json={"detail": "Not found"})
        auth_header = request.headers.get("authorization", "")
        token = auth_header.removeprefix("Bearer ").strip()
        user = _USERS_BY_TOKEN.get(token)
        if not user:
            return httpx.Response(401, json={"error": {"code": "UNAUTHORIZED", "message": "Invalid token"}})
        return httpx.Response(200, json=user)

    return httpx.MockTransport(handler)


def make_project_transport(
    *,
    force_status: int | None = None,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if force_status is not None:
            return httpx.Response(force_status, json={"detail": "Forced project error"})
        auth_header = request.headers.get("authorization", "")
        token = auth_header.removeprefix("Bearer ").strip()
        user = _USERS_BY_TOKEN.get(token)
        if not user:
            return httpx.Response(401, json={"detail": "Unauthorized"})

        parts = request.url.path.strip("/").split("/")
        if len(parts) != 4 or parts[:3] != ["api", "v1", "projects"]:
            return httpx.Response(404, json={"detail": "Not found"})
        project_id = parts[3]

        if project_id == str(MISSING_PROJECT_ID):
            return httpx.Response(404, json={"error": {"code": "PROJECT_NOT_FOUND", "message": "Missing"}})

        if project_id in {str(PROJECT_ID), str(DISCONNECTED_PROJECT_ID)}:
            user_id = user["id"]
            role = user["role"]
            if role == "OWNER" and user_id != str(OWNER_ID):
                return httpx.Response(403, json={"error": {"code": "FORBIDDEN", "message": "Not owner"}})
            active_assignment = None
            if user_id == str(ASSIGNED_FREELANCER_ID):
                active_assignment = {
                    "id": "99999999-9999-9999-9999-999999999999",
                    "freelancer_id": str(ASSIGNED_FREELANCER_ID),
                    "status": "ACTIVE",
                }
            # Mirrors real Project Service: OPEN project returns 200 to unassigned freelancer
            # with active_assignment=None, so Code Context MUST enforce assignment itself!
            return httpx.Response(
                200,
                json={
                    "id": project_id,
                    "owner_id": str(OWNER_ID),
                    "status": "IN_PROGRESS" if user_id == str(ASSIGNED_FREELANCER_ID) else "OPEN",
                    "active_assignment": active_assignment,
                },
            )

        if project_id == str(OTHER_PROJECT_ID):
            if user["id"] != str(OTHER_OWNER_ID) and user["role"] != "ADMIN":
                return httpx.Response(403, json={"error": {"code": "FORBIDDEN", "message": "Forbidden"}})
            return httpx.Response(
                200,
                json={
                    "id": project_id,
                    "owner_id": str(OTHER_OWNER_ID),
                    "status": "OPEN",
                    "active_assignment": None,
                },
            )

        return httpx.Response(404, json={"detail": "Unknown project"})

    return httpx.MockTransport(handler)


def make_github_service_transport(
    repos: dict[str, FakeGitHubRepoState] | None = None,
) -> httpx.MockTransport:
    state_map = repos or {
        str(PROJECT_ID): FakeGitHubRepoState(files=build_sample_repo_files()),
        str(OTHER_PROJECT_ID): FakeGitHubRepoState(
            full_name="other/isolated-repo",
            github_repository_id=123456,
            files={
                "secret_module.py": FakeRepoFile(
                    content="def secret_algorithm() -> str:\n    return 'PROJECT_B_ONLY_TOKEN'\n",
                    sha="sha-proj-b",
                )
            },
        ),
        str(DISCONNECTED_PROJECT_ID): FakeGitHubRepoState(connected=False),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        parts = request.url.path.strip("/").split("/")
        if len(parts) != 6 or parts[:3] != ["api", "v1", "projects"] or parts[4] != "github":
            return httpx.Response(404, json={"detail": "Not found"})

        project_id = parts[3]
        endpoint = parts[5]
        repo = state_map.get(project_id)
        if repo is None:
            return httpx.Response(404, json={"error": {"code": "PROJECT_NOT_FOUND", "message": "Missing"}})

        if endpoint == "status":
            if not repo.connected:
                return httpx.Response(
                    200,
                    json={
                        "project_id": project_id,
                        "connected": False,
                        "connection_status": "NOT_CONNECTED",
                        "repository": None,
                    },
                )
            return httpx.Response(
                200,
                json={
                    "project_id": project_id,
                    "connected": True,
                    "connection_status": "ACTIVE",
                    "repository": {
                        "github_repository_id": repo.github_repository_id,
                        "full_name": repo.full_name,
                        "default_branch": repo.default_branch,
                    },
                },
            )

        if not repo.connected:
            return httpx.Response(
                404,
                json={"error": {"code": "REPOSITORY_NOT_CONNECTED", "message": "Not connected"}},
            )

        if endpoint == "branches":
            items = [
                {"name": name, "commit_sha": sha, "protected": name == repo.default_branch}
                for name, sha in repo.branches.items()
            ]
            return httpx.Response(
                200,
                json={
                    "project_id": project_id,
                    "full_name": repo.full_name,
                    "items": items,
                    "page": 1,
                    "per_page": 100,
                    "has_next_page": False,
                },
            )

        if endpoint == "contents":
            if repo.rate_limit_contents:
                return httpx.Response(
                    429,
                    json={"error": {"code": "GITHUB_RATE_LIMIT", "message": "Rate limited"}},
                )
            req_path = (request.url.params.get("path") or "").strip("/")
            req_ref = request.url.params.get("ref")
            repo.contents_calls.append((req_path, req_ref))

            if req_path in repo.files:
                file_obj = repo.files[req_path]
                raw_bytes = len(file_obj.content.encode("utf-8"))
                return httpx.Response(
                    200,
                    json={
                        "project_id": project_id,
                        "full_name": repo.full_name,
                        "ref": req_ref or repo.default_branch,
                        "path": req_path,
                        "kind": "file",
                        "sha": file_obj.sha,
                        "size": file_obj.size if file_obj.size is not None else raw_bytes,
                        "encoding": file_obj.encoding,
                        "is_binary": file_obj.is_binary,
                        "truncated": file_obj.truncated,
                        "content": file_obj.content,
                        "entries": None,
                    },
                )

            prefix = f"{req_path}/" if req_path else ""
            seen_dirs: set[str] = set()
            entries: list[dict[str, Any]] = []
            found_any = req_path == ""
            for fpath, file_obj in repo.files.items():
                if not fpath.startswith(prefix):
                    continue
                found_any = True
                remainder = fpath[len(prefix) :]
                if "/" in remainder:
                    dir_name = remainder.split("/", 1)[0]
                    dir_full = f"{prefix}{dir_name}"
                    if dir_full not in seen_dirs:
                        seen_dirs.add(dir_full)
                        entries.append(
                            {
                                "name": dir_name,
                                "path": dir_full,
                                "type": "dir",
                                "size": 0,
                                "sha": f"tree-{dir_name}",
                            }
                        )
                else:
                    raw_bytes = len(file_obj.content.encode("utf-8"))
                    entries.append(
                        {
                            "name": remainder,
                            "path": fpath,
                            "type": "file",
                            "size": file_obj.size if file_obj.size is not None else raw_bytes,
                            "sha": file_obj.sha,
                        }
                    )

            if not found_any:
                return httpx.Response(404, json={"error": {"code": "GITHUB_NOT_FOUND", "message": "Missing path"}})

            return httpx.Response(
                200,
                json={
                    "project_id": project_id,
                    "full_name": repo.full_name,
                    "ref": req_ref or repo.default_branch,
                    "path": req_path,
                    "kind": "dir",
                    "entries": entries,
                },
            )

        return httpx.Response(404, json={"detail": "Not found"})

    return httpx.MockTransport(handler)


class CallableMockLLM(MockLLMHandler):
    """Flexible async mock LLM handler for grounding and prompt-injection tests."""

    def __init__(self, response_fn) -> None:
        self._response_fn = response_fn

    async def complete(self, *, system_prompt: str, user_prompt: str) -> str:
        return self._response_fn(system_prompt, user_prompt)


@pytest.fixture
def base_test_settings(tmp_path: Path) -> Settings:
    db_file = tmp_path / "code_context_test.db"
    chroma_dir = tmp_path / "chroma_test"
    return Settings(
        APP_ENV="test",
        DEBUG=False,
        DATABASE_URL=f"sqlite+aiosqlite:///{db_file.as_posix()}",
        IDENTITY_SERVICE_URL="http://identity.test",
        PROJECT_SERVICE_URL="http://project.test",
        GITHUB_SERVICE_URL="http://github.test",
        CHROMA_MODE="persistent",
        CHROMA_PERSIST_DIRECTORY=str(chroma_dir),
        EMBEDDING_PROVIDER="mock",
        LLM_PROVIDER="mock",
    )


async def init_test_schema(settings: Settings) -> None:
    engine = build_async_engine(settings)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("SELECT 1"))
    await engine.dispose()


@pytest_asyncio.fixture
async def test_app(base_test_settings: Settings) -> FastAPI:
    await init_test_schema(base_test_settings)
    from app.main import create_app

    return create_app(
        settings=base_test_settings,
        identity_transport=make_identity_transport(),
        project_transport=make_project_transport(),
        github_transport=make_github_service_transport(),
    )


@pytest_asyncio.fixture
async def async_client(test_app: FastAPI):
    async with test_app.router.lifespan_context(test_app):
        transport = httpx.ASGITransport(app=test_app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://code-context.test",
        ) as client:
            yield client


def auth_headers(token: str, request_id: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if request_id:
        headers["X-Request-ID"] = request_id
    return headers


def pretty_json(data: object) -> str:
    return json.dumps(data, indent=2)

