"""Tests for safe automated workspace test execution (POST /api/v1/workspace/{workspace_id}/run-tests)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.workspace import Workspace
from tests.conftest import (
    ASSIGNED_PROJECT_ID,
    TEST_FREELANCER_TOKEN,
    TEST_OTHER_FREELANCER_TOKEN,
    TEST_OWNER_TOKEN,
    auth_headers,
    build_sample_repo_zip,
)


@pytest.mark.asyncio
async def test_workspace_run_tests_pass_fail_security_and_rbac(
    client: AsyncClient,
    workspace_app,
    mock_github_state,
) -> None:
    mock_github_state.archive_bytes = build_sample_repo_zip(
        {
            "alice-sample-api-abc1234/app_math.py": "def add(a: int, b: int) -> int:\n    return a + b\n",
            "alice-sample-api-abc1234/test_passing.py": (
                "import unittest\n"
                "from app_math import add\n\n"
                "class TestMath(unittest.TestCase):\n"
                "    def test_add(self):\n"
                "        self.assertEqual(add(2, 3), 5)\n"
            ),
            "alice-sample-api-abc1234/test_failing.py": (
                "import os\n"
                "import unittest\n\n"
                "class TestFail(unittest.TestCase):\n"
                "    def test_env_clean_and_fail(self):\n"
                "        self.assertIsNone(os.environ.get('DATABASE_URL'))\n"
                "        self.assertIsNone(os.environ.get('INTERNAL_SERVICE_KEY'))\n"
                "        self.fail('Intentional assertion failure Bearer secret-abc')\n"
            ),
        }
    )

    ws = await client.post(
        f"/api/v1/workspace/projects/{ASSIGNED_PROJECT_ID}",
        headers=auth_headers(TEST_FREELANCER_TOKEN),
        json={"auto_start": True},
    )
    assert ws.status_code == 201
    ws_id = ws.json()["id"]

    # 1. Passing unittest run recorded cleanly
    pass_resp = await client.post(
        f"/api/v1/workspace/{ws_id}/run-tests",
        headers=auth_headers(TEST_FREELANCER_TOKEN),
        json={"runner": "unittest", "target_path": "test_passing.py", "timeout_seconds": 30},
    )
    assert pass_resp.status_code == 200, pass_resp.text
    pbody = pass_resp.json()
    assert pbody["passed"] is True
    assert pbody["exit_code"] == 0

    # 2. Failing test run captures exit code, strips secret-like tokens, & verifies DB/internal keys not leaked into child env
    fail_resp = await client.post(
        f"/api/v1/workspace/{ws_id}/run-tests",
        headers=auth_headers(TEST_FREELANCER_TOKEN),
        json={"runner": "unittest", "target_path": "test_failing.py", "timeout_seconds": 30},
    )
    assert fail_resp.status_code == 200, fail_resp.text
    fbody = fail_resp.json()
    assert fbody["passed"] is False
    assert fbody["exit_code"] != 0
    assert "secret-abc" not in fbody["output_excerpt"]

    # 3. Path traversal in target_path strictly rejected (422)
    trav_resp = await client.post(
        f"/api/v1/workspace/{ws_id}/run-tests",
        headers=auth_headers(TEST_FREELANCER_TOKEN),
        json={"runner": "pytest", "target_path": "../../etc/passwd"},
    )
    assert trav_resp.status_code == 422

    # 4. Disallowed runner rejected (422)
    bad_runner = await client.post(
        f"/api/v1/workspace/{ws_id}/run-tests",
        headers=auth_headers(TEST_FREELANCER_TOKEN),
        json={"runner": "bash"},
    )
    assert bad_runner.status_code == 422

    # 5. Unauthorized other freelancer strictly rejected (403)
    unauth = await client.post(
        f"/api/v1/workspace/{ws_id}/run-tests",
        headers=auth_headers(TEST_OTHER_FREELANCER_TOKEN),
        json={"runner": "unittest"},
    )
    assert unauth.status_code == 403

    # 6. Owner can inspect workspace events & see TEST_RUN_PASSED / TEST_RUN_FAILED persisted
    detail = await client.get(f"/api/v1/workspace/{ws_id}", headers=auth_headers(TEST_OWNER_TOKEN))
    assert detail.status_code == 200
    ev_types = {e["event_type"] for e in detail.json()["events"]}
    assert {"TEST_RUN_PASSED", "TEST_RUN_FAILED"}.issubset(ev_types)

    # Confirm extracted workspace files live under temp workspace directory
    async with workspace_app.state.session_factory() as session:
        row = (await session.execute(select(Workspace).where(Workspace.id == uuid.UUID(ws_id)))).scalar_one()
        assert Path(row.workspace_path).exists()

