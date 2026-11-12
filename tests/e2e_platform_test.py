"""Full Live Multi-Process End-to-End Platform Lifecycle Integration Test.

Starts all 13 CodeBridge FastAPI services (API Gateway + 12 domain microservices)
as real independent Uvicorn HTTP processes on isolated ephemeral TCP ports, backed by
per-service Alembic-migrated SQLite databases and a real local HTTP Mock GitHub server.
Executes the entire 20-step platform lifecycle end-to-end strictly through API Gateway.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import zipfile
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _alloc_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _make_sample_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("alice-gh-sample-api-abc1234/README.md", "# Sample API\nCodeBridge E2E Repo\n")
        zf.writestr(
            "alice-gh-sample-api-abc1234/app.py",
            'def calculate_payout(amount: float) -> float:\n    """Verify payment settlement calculation."""\n    return round(amount, 2)\n',
        )
        zf.writestr(
            "alice-gh-sample-api-abc1234/test_app.py",
            "import unittest\nfrom app import calculate_payout\n\nclass TestApp(unittest.TestCase):\n    def test_rounding(self):\n        self.assertEqual(calculate_payout(100.126), 100.13)\n",
        )
    return buf.getvalue()


def _start_mock_github_server(port: int) -> ThreadingHTTPServer:
    zip_bytes = _make_sample_zip()
    files = {
        "README.md": b"# Sample API\nCodeBridge E2E Repo\n",
        "app.py": b'def calculate_payout(amount: float) -> float:\n    """Verify payment settlement calculation."""\n    return round(amount, 2)\n',
    }
    repo_obj = {
        "id": 9001,
        "name": "sample-api",
        "full_name": "alice-gh/sample-api",
        "owner": {"login": "alice-gh"},
        "private": True,
        "default_branch": "main",
        "description": "CodeBridge E2E Repository",
        "html_url": "https://github.com/alice-gh/sample-api",
        "archived": False,
        "fork": False,
        "permissions": {"admin": True, "maintain": True, "push": True, "pull": True},
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:  # noqa: A002
            return

        def _send_json(self, code: int, payload: object) -> None:
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            _ = self.rfile.read(length) if length else b""
            if self.path.startswith("/login/oauth/access_token"):
                self._send_json(
                    200,
                    {
                        "access_token": "gho_e2e_mock_oauth_token_123456",
                        "scope": "repo read:user",
                        "token_type": "bearer",
                    },
                )
                return
            self._send_json(404, {"message": "Not Found"})

        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            if path == "/user":
                self._send_json(200, {"id": 424242, "login": "alice-gh", "name": "Alice GitHub"})
                return
            if path == "/user/repos":
                self._send_json(200, [repo_obj])
                return
            if path == "/repos/alice-gh/sample-api":
                self._send_json(200, repo_obj)
                return
            if path == "/repos/alice-gh/sample-api/branches":
                self._send_json(
                    200,
                    [{"name": "main", "commit": {"sha": "abc1234000000000000000000000000000000001"}, "protected": True}],
                )
                return
            if path == "/repos/alice-gh/sample-api/commits":
                self._send_json(
                    200,
                    [
                        {
                            "sha": "abc1234000000000000000000000000000000001",
                            "html_url": "https://github.com/alice-gh/sample-api/commit/abc1234",
                            "commit": {
                                "message": "Fix payment payout rounding",
                                "author": {"name": "Alice", "date": "2026-10-01T12:00:00Z"},
                            },
                            "author": {"login": "alice-gh"},
                        }
                    ],
                )
                return
            if path == "/repos/alice-gh/sample-api/contents":
                self._send_json(
                    200,
                    [
                        {"name": "README.md", "path": "README.md", "type": "file", "size": len(files["README.md"]), "sha": "b1"},
                        {"name": "app.py", "path": "app.py", "type": "file", "size": len(files["app.py"]), "sha": "b2"},
                    ],
                )
                return
            if path.startswith("/repos/alice-gh/sample-api/contents/"):
                rel = urllib.parse.unquote(path[len("/repos/alice-gh/sample-api/contents/") :])
                if rel in files:
                    content_bytes = files[rel]
                    self._send_json(
                        200,
                        {
                            "type": "file",
                            "name": rel.split("/")[-1],
                            "path": rel,
                            "sha": f"sha-{rel}",
                            "size": len(content_bytes),
                            "encoding": "base64",
                            "content": base64.b64encode(content_bytes).decode("ascii"),
                        },
                    )
                    return
                self._send_json(404, {"message": "Not Found"})
                return
            if path.startswith("/repos/alice-gh/sample-api/zipball"):
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Length", str(len(zip_bytes)))
                self.end_headers()
                self.wfile.write(zip_bytes)
                return
            self._send_json(404, {"message": "Not Found"})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _run_alembic_and_seed_admin(service_dir: Path, db_url: str, *, seed_admin: bool = False) -> None:
    alembic_exe = service_dir / ".venv" / "Scripts" / "alembic.exe"
    python_exe = service_dir / ".venv" / "Scripts" / "python.exe"
    env = os.environ.copy()
    env["DATABASE_URL"] = db_url
    subprocess.run(
        [str(alembic_exe), "upgrade", "head"],
        cwd=str(service_dir),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    if seed_admin:
        seed_script = """
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.core.config import load_settings
from app.core.security import hash_password
from app.models.user import User, UserRole

async def main():
    settings = load_settings()
    engine = create_async_engine(settings.DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        admin = User(
            full_name="Platform Admin",
            email="admin.e2e@example.com",
            password_hash=hash_password("AdminPass123!"),
            role=UserRole.ADMIN,
            is_active=True,
            is_verified=True,
        )
        s.add(admin)
        await s.commit()
    await engine.dispose()

asyncio.run(main())
"""
        subprocess.run(
            [str(python_exe), "-c", seed_script],
            cwd=str(service_dir),
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )


def _wait_for_health(base_url: str, name: str, proc: subprocess.Popen, timeout_s: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            out, err = proc.communicate()
            raise RuntimeError(f"{name} exited early ({proc.returncode}): {out}\n{err}")
        try:
            resp = httpx.get(f"{base_url}/health", timeout=1.5)
            if resp.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.2)
    raise TimeoutError(f"Timed out waiting for {name} /health at {base_url}")


def test_full_platform_20_step_e2e_lifecycle() -> None:
    tmp_dir = Path(tempfile.mkdtemp(prefix="cb_e2e_"))
    gh_mock_port = _alloc_port()
    ports = {
        "api-gateway": _alloc_port(),
        "identity-service": _alloc_port(),
        "project-service": _alloc_port(),
        "github-service": _alloc_port(),
        "code-context-service": _alloc_port(),
        "workspace-service": _alloc_port(),
        "review-service": _alloc_port(),
        "verification-service": _alloc_port(),
        "payment-service": _alloc_port(),
        "notification-service": _alloc_port(),
        "admin-service": _alloc_port(),
        "audit-service": _alloc_port(),
        "observability-service": _alloc_port(),
    }
    urls = {k: f"http://127.0.0.1:{p}" for k, p in ports.items()}

    db_services = [k for k in ports if k != "api-gateway"]
    db_urls = {
        k: f"sqlite+aiosqlite:///{(tmp_dir / f'{k}.db').as_posix()}"
        for k in db_services
    }

    gh_server = _start_mock_github_server(gh_mock_port)
    procs: list[tuple[str, subprocess.Popen]] = []

    try:
        # 1. Run real Alembic migrations for all 12 DB services + seed ADMIN in identity-service
        for svc in db_services:
            _run_alembic_and_seed_admin(
                REPO_ROOT / "services" / svc,
                db_urls[svc],
                seed_admin=(svc == "identity-service"),
            )

        shared_env = {
            "APP_ENV": "test",
            "DEBUG": "false",
            "LOG_LEVEL": "WARNING",
            "INTERNAL_SERVICE_KEY": "e2e-internal-service-key-change-me-32",
            "VERIFICATION_SIGNING_SECRET": "e2e-verification-signing-secret-32chars",
            "API_GATEWAY_URL": urls["api-gateway"],
            "IDENTITY_SERVICE_URL": urls["identity-service"],
            "PROJECT_SERVICE_URL": urls["project-service"],
            "GITHUB_SERVICE_URL": urls["github-service"],
            "CODE_CONTEXT_SERVICE_URL": urls["code-context-service"],
            "WORKSPACE_SERVICE_URL": urls["workspace-service"],
            "REVIEW_SERVICE_URL": urls["review-service"],
            "VERIFICATION_SERVICE_URL": urls["verification-service"],
            "PAYMENT_SERVICE_URL": urls["payment-service"],
            "NOTIFICATION_SERVICE_URL": urls["notification-service"],
            "ADMIN_SERVICE_URL": urls["admin-service"],
            "AUDIT_SERVICE_URL": urls["audit-service"],
            "OBSERVABILITY_SERVICE_URL": urls["observability-service"],
            "GITHUB_TEST_API_BASE_URL": f"http://127.0.0.1:{gh_mock_port}",
            "GITHUB_TEST_OAUTH_TOKEN_URL": f"http://127.0.0.1:{gh_mock_port}/login/oauth/access_token",
            "GITHUB_OAUTH_CALLBACK_URL": f"{urls['api-gateway']}/api/v1/github/oauth/callback",
            "CHROMA_PERSIST_DIRECTORY": (tmp_dir / "chroma_data").as_posix(),
            "EMBEDDING_PROVIDER": "mock",
            "LLM_PROVIDER": "none",
            "WORKSPACE_DOCKER_MODE": "mock",
            "WORKSPACE_STORAGE_ROOT": (tmp_dir / "workspace_data").as_posix(),
            "REQUEST_TIMEOUT_SECONDS": "20.0",
            "SERVICE_CLIENT_TIMEOUT_SECONDS": "10.0",
            "OBSERVABILITY_PROBE_TIMEOUT_SECONDS": "3.0",
        }

        launch_order = db_services + ["api-gateway"]
        for name in launch_order:
            svc_dir = REPO_ROOT / ("api-gateway" if name == "api-gateway" else f"services/{name}")
            py_exe = svc_dir / ".venv" / "Scripts" / "python.exe"
            env = os.environ.copy()
            env.update(shared_env)
            env["PORT"] = str(ports[name])
            if name in db_urls:
                env["DATABASE_URL"] = db_urls[name]
            proc = subprocess.Popen(
                [
                    str(py_exe),
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(ports[name]),
                    "--log-level",
                    "warning",
                ],
                cwd=str(svc_dir),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            procs.append((name, proc))

        for name, proc in procs:
            _wait_for_health(urls[name], name, proc)

        # Verify /ready returns HTTP 200 across all 13 services (gateway + 12 microservices)
        for name, base_url in urls.items():
            ready_res = httpx.get(f"{base_url}/ready", timeout=5.0)
            assert ready_res.status_code == 200, f"{name} /ready failed: {ready_res.text}"

        gw = urls["api-gateway"]
        with httpx.Client(base_url=gw, timeout=20.0) as client:
            # Step 1: Register Owner + Two Freelancers (assigned + unrelated)
            r_own = client.post(
                "/api/v1/auth/register",
                json={
                    "full_name": "Alice Owner",
                    "email": "alice.e2e@example.com",
                    "password": "OwnerPassword123!",
                    "role": "OWNER",
                },
            )
            assert r_own.status_code == 201, r_own.text
            owner_id = r_own.json()["id"]

            r_fr = client.post(
                "/api/v1/auth/register",
                json={
                    "full_name": "Charlie Freelancer",
                    "email": "charlie.e2e@example.com",
                    "password": "FreelancerPass123!",
                    "role": "FREELANCER",
                },
            )
            assert r_fr.status_code == 201, r_fr.text
            freelancer_id = r_fr.json()["id"]

            r_other_fr = client.post(
                "/api/v1/auth/register",
                json={
                    "full_name": "Dana Freelancer",
                    "email": "dana.e2e@example.com",
                    "password": "FreelancerPass123!",
                    "role": "FREELANCER",
                },
            )
            assert r_other_fr.status_code == 201, r_other_fr.text

            # Public ADMIN self-registration strictly rejected (403)
            assert (
                client.post(
                    "/api/v1/auth/register",
                    json={
                        "full_name": "Fake Admin",
                        "email": "fakeadmin@example.com",
                        "password": "AdminPassword123!",
                        "role": "ADMIN",
                    },
                ).status_code
                == 403
            )

            # Step 2: Login Owner, Freelancer, Admin
            login_owner = client.post(
                "/api/v1/auth/login",
                json={"email": "alice.e2e@example.com", "password": "OwnerPassword123!"},
            )
            assert login_owner.status_code == 200, login_owner.text
            tok_owner = login_owner.json()["access_token"]

            login_freelancer = client.post(
                "/api/v1/auth/login",
                json={"email": "charlie.e2e@example.com", "password": "FreelancerPass123!"},
            )
            assert login_freelancer.status_code == 200, login_freelancer.text
            tok_freelancer = login_freelancer.json()["access_token"]

            login_other_fr = client.post(
                "/api/v1/auth/login",
                json={"email": "dana.e2e@example.com", "password": "FreelancerPass123!"},
            )
            assert login_other_fr.status_code == 200, login_other_fr.text
            tok_other_freelancer = login_other_fr.json()["access_token"]

            login_admin = client.post(
                "/api/v1/auth/login",
                json={"email": "admin.e2e@example.com", "password": "AdminPass123!"},
            )
            assert login_admin.status_code == 200, login_admin.text
            tok_admin = login_admin.json()["access_token"]

            h_owner = {"Authorization": f"Bearer {tok_owner}"}
            h_freelancer = {"Authorization": f"Bearer {tok_freelancer}"}
            h_other_freelancer = {"Authorization": f"Bearer {tok_other_freelancer}"}
            h_admin = {"Authorization": f"Bearer {tok_admin}"}

            # Step 3 & 4: Owner creates & publishes project
            deadline = (date.today() + timedelta(days=30)).isoformat()
            proj_resp = client.post(
                "/api/v1/projects",
                headers=h_owner,
                json={
                    "title": "Fix Payment Rounding Bug",
                    "description": "Ensure payout calculations round cleanly to 2 decimal places.",
                    "expected_result": "All unit tests pass and calculate_payout handles floats properly.",
                    "budget": "5000.00",
                    "currency": "INR",
                    "deadline": deadline,
                    "required_skills": ["Python", "FastAPI"],
                },
            )
            assert proj_resp.status_code == 201, proj_resp.text
            project_id = proj_resp.json()["id"]

            pub_resp = client.post(f"/api/v1/projects/{project_id}/publish", headers=h_owner)
            assert pub_resp.status_code == 200, pub_resp.text

            # Step 5: Owner connects GitHub OAuth & links sample-api repository
            authz_resp = client.get(
                f"/api/v1/github/oauth/authorize?project_id={project_id}",
                headers=h_owner,
                follow_redirects=False,
            )
            assert authz_resp.status_code == 302, authz_resp.text
            loc_qs = urllib.parse.parse_qs(urllib.parse.urlparse(authz_resp.headers["location"]).query)
            oauth_state = loc_qs["state"][0]

            cb_resp = client.get(
                f"/api/v1/github/oauth/callback?code=e2e-gh-code&state={oauth_state}",
                headers=h_owner,
            )
            assert cb_resp.status_code == 200, cb_resp.text

            connect_resp = client.post(
                f"/api/v1/projects/{project_id}/github/connect",
                headers=h_owner,
                json={"github_repository_id": 9001, "full_name": "alice-gh/sample-api"},
            )
            assert connect_resp.status_code == 201, connect_resp.text

            # Step 6: Owner indexes repository for Code Context; unassigned freelancer blocked (403) before assignment
            idx_resp = client.post(
                f"/api/v1/code-context/projects/{project_id}/index",
                headers=h_owner,
                json={"branch": "main"},
            )
            assert idx_resp.status_code == 201, idx_resp.text
            assert (
                client.post(
                    f"/api/v1/code-context/projects/{project_id}/query",
                    headers=h_freelancer,
                    json={"query": "How does calculate_payout work?"},
                ).status_code
                == 403
            )

            # Negative Check 5: Unassigned freelancer provisioning a workspace rejected (403)
            assert (
                client.post(
                    f"/api/v1/workspace/projects/{project_id}",
                    headers=h_freelancer,
                    json={"auto_start": False},
                ).status_code
                == 403
            )

            # Step 7: Owner assigns Freelancer -> Freelancer can now query Code Context RAG
            assign_resp = client.post(
                f"/api/v1/projects/{project_id}/select-freelancer",
                headers=h_owner,
                json={"freelancer_id": freelancer_id},
            )
            assert assign_resp.status_code == 201, assign_resp.text

            rag_resp = client.post(
                f"/api/v1/code-context/projects/{project_id}/query",
                headers=h_freelancer,
                json={"query": "Where is payout calculation implemented?"},
            )
            assert rag_resp.status_code == 200, rag_resp.text
            assert rag_resp.json()["data"]["grounded"] is True
            assert len(rag_resp.json()["data"]["sources"]) >= 1

            # Step 8: Owner creates & funds Escrow -> HELD
            escrow_resp = client.post(
                f"/api/v1/payment/projects/{project_id}/escrow",
                headers={**h_owner, "Idempotency-Key": "e2e-create-escrow-1"},
                json={"amount": "5000.00", "currency": "INR"},
            )
            assert escrow_resp.status_code == 201, escrow_resp.text
            escrow_id = escrow_resp.json()["id"]

            fund_resp = client.post(
                f"/api/v1/payment/escrows/{escrow_id}/fund",
                headers={**h_owner, "Idempotency-Key": "e2e-fund-escrow-1"},
                json={"hold_if_assigned": True},
            )
            assert fund_resp.status_code == 200, fund_resp.text
            assert fund_resp.json()["status"] == "HELD"

            # Step 9 & 10: Freelancer provisions, starts, and refreshes Workspace
            ws_resp = client.post(
                f"/api/v1/workspace/projects/{project_id}",
                headers=h_freelancer,
                json={"auto_start": True},
            )
            assert ws_resp.status_code == 201, ws_resp.text
            ws_id = ws_resp.json()["id"]
            assert ws_resp.json()["status"] == "RUNNING"

            ref_ws = client.post(f"/api/v1/workspace/{ws_id}/refresh", headers=h_freelancer, json={})
            assert ref_ws.status_code == 200, ref_ws.text

            # Negative Check 1: Other freelancer accessing this project's workspace rejected (403)
            assert client.get(f"/api/v1/workspace/{ws_id}", headers=h_other_freelancer).status_code == 403

            # Safe automated test execution inside provisioned workspace through API Gateway
            test_run_resp = client.post(
                f"/api/v1/workspace/{ws_id}/run-tests",
                headers=h_freelancer,
                json={"runner": "unittest", "target_path": "test_app.py", "timeout_seconds": 30},
            )
            assert test_run_resp.status_code == 200, test_run_resp.text
            assert test_run_resp.json()["passed"] is True
            assert test_run_resp.json()["exit_code"] == 0

            # Step 11: Freelancer submits v1 work
            sub1 = client.post(
                f"/api/v1/submissions/projects/{project_id}",
                headers=h_freelancer,
                json={
                    "workspace_id": ws_id,
                    "title": "v1 Payout Fix",
                    "summary": "Initial fix for calculate_payout precision.",
                    "changes_summary": "Updated app.py rounding helper.",
                    "commit_sha": "abc1234",
                    "submit_immediately": True,
                },
            )
            assert sub1.status_code == 201, sub1.text
            sub1_id = sub1.json()["id"]

            # Step 12: Premature Verification issuance & Escrow release STRICTLY REJECTED (409)
            assert (
                client.post(
                    "/api/v1/verification/records",
                    headers=h_owner,
                    json={"project_id": project_id, "submission_id": sub1_id},
                ).status_code
                == 409
            )
            assert (
                client.post(
                    f"/api/v1/payment/escrows/{escrow_id}/release",
                    headers={**h_owner, "Idempotency-Key": "e2e-premature-release"},
                    json={"reason": "Should fail before approval"},
                ).status_code
                == 409
            )

            # Negative Check 2: Freelancer approving own submission STRICTLY REJECTED (403)
            assert (
                client.post(f"/api/v1/review/submissions/{sub1_id}/start", headers=h_freelancer).status_code == 403
            )
            assert (
                client.post(
                    f"/api/v1/review/submissions/{sub1_id}/decision",
                    headers=h_freelancer,
                    json={"decision": "APPROVED", "feedback": "Self approve attempt", "checklist_passed": True},
                ).status_code
                == 403
            )

            # Step 13: Owner requests changes -> Freelancer submits v2 -> Owner approves (syncs project to COMPLETED)
            assert client.post(f"/api/v1/review/submissions/{sub1_id}/start", headers=h_owner).status_code == 200
            chg_resp = client.post(
                f"/api/v1/review/submissions/{sub1_id}/decision",
                headers=h_owner,
                json={"decision": "CHANGES_REQUESTED", "feedback": "Please add docstrings and verify edge cases.", "checklist_passed": False},
            )
            assert chg_resp.status_code == 200, chg_resp.text

            sub2 = client.post(
                f"/api/v1/submissions/projects/{project_id}",
                headers=h_freelancer,
                json={
                    "workspace_id": ws_id,
                    "title": "v2 Final Payout Fix",
                    "summary": "Added edge-case handling and documentation.",
                    "changes_summary": "Updated app.py with docstrings and full test run.",
                    "commit_sha": "abc1234",
                    "submit_immediately": True,
                },
            )
            assert sub2.status_code == 201, sub2.text
            sub2_id = sub2.json()["id"]
            assert sub2.json()["version_number"] == 2

            assert client.post(f"/api/v1/review/submissions/{sub2_id}/start", headers=h_owner).status_code == 200
            app_resp = client.post(
                f"/api/v1/review/submissions/{sub2_id}/decision",
                headers=h_owner,
                json={"decision": "APPROVED", "feedback": "Verified clean fix!", "checklist_passed": True},
            )
            assert app_resp.status_code == 200, app_resp.text
            assert app_resp.json()["status"] == "APPROVED"

            proj_after = client.get(f"/api/v1/projects/{project_id}", headers=h_owner)
            assert proj_after.status_code == 200
            assert proj_after.json()["status"] == "COMPLETED"

            # Step 14 & 15: Owner issues Verified Work Record & public unauthenticated lookup succeeds
            ver_resp = client.post(
                "/api/v1/verification/records",
                headers=h_owner,
                json={"project_id": project_id, "submission_id": sub2_id},
            )
            assert ver_resp.status_code == 201, ver_resp.text
            pub_ver_id = ver_resp.json()["public_verification_id"]
            assert pub_ver_id.startswith("cbv_")
            assert ver_resp.json()["signature_valid"] is True

            pub_lookup = client.get(f"/api/v1/verification/public/{pub_ver_id}")
            assert pub_lookup.status_code == 200, pub_lookup.text
            assert pub_lookup.json()["is_valid"] is True
            assert "owner_id" not in pub_lookup.json()

            # Freelancer Verified Work Profile via Gateway (/api/v1/profile/verified-work) & public portfolio
            my_verified = client.get("/api/v1/profile/verified-work", headers=h_freelancer)
            assert my_verified.status_code == 200, my_verified.text
            assert my_verified.json()["total"] == 1
            assert my_verified.json()["items"][0]["public_verification_id"] == pub_ver_id

            pub_portfolio = client.get(f"/api/v1/verification/freelancers/{freelancer_id}")
            assert pub_portfolio.status_code == 200, pub_portfolio.text
            assert pub_portfolio.json()["total"] == 1

            # Step 16 & 17a: Open real-time WebSocket via API Gateway before releasing Escrow
            async def _release_and_receive_ws() -> dict:
                import websockets

                ws_url = f"ws://127.0.0.1:{ports['api-gateway']}/api/v1/notifications/ws?token={tok_freelancer}"
                async with websockets.connect(ws_url) as ws:
                    welcome = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
                    assert welcome["event"] == "connected"
                    await ws.send("ping")
                    pong = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
                    assert pong["event"] == "pong"
                    async with httpx.AsyncClient(base_url=gw, timeout=20.0) as ac:
                        rel_resp = await ac.post(
                            f"/api/v1/payment/escrows/{escrow_id}/release",
                            headers={**h_owner, "Idempotency-Key": "e2e-release-final"},
                            json={"reason": "Approved final submission v2"},
                        )
                        assert rel_resp.status_code == 200, rel_resp.text
                        assert rel_resp.json()["status"] == "RELEASED"
                    while True:
                        raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                        frame = json.loads(raw)
                        if frame.get("event") == "notification.created":
                            return frame

            ws_frame = asyncio.run(_release_and_receive_ws())
            assert ws_frame["data"]["event_type"] == "ESCROW_RELEASED"

            # Negative Check 6: Duplicate payment release with a fresh Idempotency-Key strictly rejected (409)
            assert (
                client.post(
                    f"/api/v1/payment/escrows/{escrow_id}/release",
                    headers={**h_owner, "Idempotency-Key": "e2e-duplicate-release"},
                    json={"reason": "Duplicate payout attempt"},
                ).status_code
                == 409
            )

            # Step 17: Freelancer & Owner receive Notifications & mark read
            fr_notifs = client.get("/api/v1/notifications", headers=h_freelancer)
            assert fr_notifs.status_code == 200, fr_notifs.text
            assert fr_notifs.json()["total"] >= 2
            assert client.post("/api/v1/notifications/read-all", headers=h_freelancer).status_code == 200

            # Step 18: Audit logs recorded across review, verification, and payment
            proj_audit = client.get(f"/api/v1/audit/projects/{project_id}", headers=h_owner)
            assert proj_audit.status_code == 200, proj_audit.text
            assert proj_audit.json()["total"] >= 4
            assert owner_id != freelancer_id

            # Step 19: Admin inspects users, projects, audit logs, moderates report, suspends & restores freelancer
            assert client.get("/api/v1/admin/users", headers=h_admin).json()["total"] >= 3
            assert client.get("/api/v1/admin/projects", headers=h_admin).json()["total"] >= 1
            assert client.get("/api/v1/admin/audit-logs", headers=h_admin).json()["total"] >= 4

            rep = client.post(
                "/api/v1/admin/reports",
                headers=h_admin,
                json={"target_type": "USER", "target_id": freelancer_id, "reason": "Routine compliance audit", "severity": "LOW"},
            )
            assert rep.status_code == 201
            assert (
                client.post(
                    f"/api/v1/admin/reports/{rep.json()['id']}/resolve",
                    headers=h_admin,
                    json={"status": "RESOLVED", "resolution_notes": "All checks cleared."},
                ).status_code
                == 200
            )
            assert (
                client.post(
                    f"/api/v1/admin/users/{freelancer_id}/suspend",
                    headers=h_admin,
                    json={"reason": "Temporary compliance hold"},
                ).json()["is_active"]
                is False
            )
            assert (
                client.post(
                    f"/api/v1/admin/users/{freelancer_id}/restore",
                    headers=h_admin,
                    json={"reason": "Hold lifted"},
                ).json()["is_active"]
                is True
            )

            # Step 20: Observability aggregates health/readiness across Gateway + all 12 services
            obs = client.get("/api/v1/observability/services", headers=h_admin)
            assert obs.status_code == 200, obs.text
            obs_body = obs.json()
            assert obs_body["total_services"] == 13
            assert obs_body["healthy_count"] == 13
            assert obs_body["ready_count"] == 13
            assert obs_body["overall_status"] == "healthy"

    finally:
        gh_server.shutdown()
        gh_server.server_close()
        for _name, proc in procs:
            proc.terminate()
        for _name, proc in procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
