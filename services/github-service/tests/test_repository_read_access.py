"""Tests for read-only repository access (/branches, /contents, /commits) & path/size safety."""

from __future__ import annotations

import base64

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.models.base import Base
from tests.conftest import (
    PROJECT_ID,
    TEST_ADMIN_TOKEN,
    TEST_FREELANCER_TOKEN,
    TEST_OTHER_OWNER_TOKEN,
    TEST_OWNER_TOKEN,
    MockGitHubState,
    MockIdentityState,
    MockProjectState,
    auth_headers,
    build_test_settings,
    connect_owner_github_account,
)


@pytest.mark.asyncio
async def test_branches_contents_and_commits_read_access(
    client: AsyncClient,
) -> None:
    await connect_owner_github_account(client)
    conn_resp = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/github/connect",
        json={"github_repository_id": 1001, "full_name": "alice-gh/sample-api"},
        headers=auth_headers(TEST_OWNER_TOKEN),
    )
    assert conn_resp.status_code == 201

    # Branches
    branches = await client.get(
        f"/api/v1/projects/{PROJECT_ID}/github/branches",
        headers=auth_headers(TEST_OWNER_TOKEN),
    )
    assert branches.status_code == 200
    assert len(branches.json()["items"]) == 2
    assert branches.json()["items"][0]["name"] == "main"
    assert branches.json()["items"][0]["protected"] is True

    # Directory contents (root)
    root_dir = await client.get(
        f"/api/v1/projects/{PROJECT_ID}/github/contents",
        headers=auth_headers(TEST_OWNER_TOKEN),
    )
    assert root_dir.status_code == 200
    assert root_dir.json()["kind"] == "dir"
    assert len(root_dir.json()["entries"]) == 2

    # Text file contents decoded safely
    readme = await client.get(
        f"/api/v1/projects/{PROJECT_ID}/github/contents",
        params={"path": "README.md", "ref": "main"},
        headers=auth_headers(TEST_OWNER_TOKEN),
    )
    assert readme.status_code == 200
    readme_body = readme.json()
    assert readme_body["kind"] == "file"
    assert readme_body["is_binary"] is False
    assert readme_body["truncated"] is False
    assert readme_body["content"] == "# Sample API\nHello"

    # Binary file safely flagged without returning binary garbage
    binary_file = await client.get(
        f"/api/v1/projects/{PROJECT_ID}/github/contents",
        params={"path": "assets/logo.png"},
        headers=auth_headers(TEST_OWNER_TOKEN),
    )
    assert binary_file.status_code == 200
    assert binary_file.json()["is_binary"] is True
    assert binary_file.json()["content"] is None
    assert binary_file.json()["encoding"] == "binary"

    # Commits
    commits = await client.get(
        f"/api/v1/projects/{PROJECT_ID}/github/commits",
        params={"sha": "main"},
        headers=auth_headers(TEST_OWNER_TOKEN),
    )
    assert commits.status_code == 200
    assert commits.json()["items"][0]["short_sha"] == "abc1234"
    assert commits.json()["items"][0]["message"] == "Initial API scaffold"


@pytest.mark.asyncio
async def test_rejects_path_traversal_bad_refs_and_non_owner_reads(
    client: AsyncClient,
    mock_github_state: MockGitHubState,
) -> None:
    await connect_owner_github_account(client)
    await client.post(
        f"/api/v1/projects/{PROJECT_ID}/github/connect",
        json={"github_repository_id": 1001, "full_name": "alice-gh/sample-api"},
        headers=auth_headers(TEST_OWNER_TOKEN),
    )

    calls_before = len(mock_github_state.api_calls)

    for bad_path in (
        "../secrets.env",
        "src/../../etc/passwd",
        "%2e%2e/secret",
        "src\\file.py",
        "https://evil.example.com/steal",
        "src/file.py?raw=1",
    ):
        resp = await client.get(
            f"/api/v1/projects/{PROJECT_ID}/github/contents",
            params={"path": bad_path},
            headers=auth_headers(TEST_OWNER_TOKEN),
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "INVALID_PATH"

    for bad_ref in ("-c core.fsmonitor=evil", "main..dev", "heads//main", "/badref"):
        resp = await client.get(
            f"/api/v1/projects/{PROJECT_ID}/github/commits",
            params={"sha": bad_ref},
            headers=auth_headers(TEST_OWNER_TOKEN),
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "INVALID_REF"

    # None of those invalid path/ref inputs ever reached GitHub API
    assert len(mock_github_state.api_calls) == calls_before

    # Non-owner access denied
    for token in (TEST_FREELANCER_TOKEN, TEST_OTHER_OWNER_TOKEN, TEST_ADMIN_TOKEN):
        for endpoint in ("branches", "contents", "commits"):
            denied = await client.get(
                f"/api/v1/projects/{PROJECT_ID}/github/{endpoint}",
                headers=auth_headers(token),
            )
            assert denied.status_code == 403


@pytest.mark.asyncio
async def test_contents_truncates_oversized_file_and_directory_safely(
    tmp_path,
    mock_identity_state: MockIdentityState,
    mock_project_state: MockProjectState,
    mock_github_state: MockGitHubState,
) -> None:
    db_file = (tmp_path / "bounded_contents.db").as_posix()
    settings = build_test_settings(
        db_file,
        GITHUB_CONTENTS_MAX_BYTES=1024,
        GITHUB_CONTENTS_MAX_ENTRIES=2,
    )
    mock_github_state.contents_by_path["huge.txt"] = {
        "type": "file",
        "name": "huge.txt",
        "path": "huge.txt",
        "sha": "blob-huge",
        "size": 2500,
        "encoding": "base64",
        "content": base64.b64encode(b"A" * 2500).decode("ascii"),
    }
    mock_github_state.contents_by_path["many"] = [
        {"name": f"f{i}.py", "path": f"many/f{i}.py", "type": "file", "size": 10, "sha": f"s{i}"}
        for i in range(5)
    ]

    app = create_app(
        settings=settings,
        identity_transport=mock_identity_state.build_transport(),
        project_transport=mock_project_state.build_transport(),
        github_transport=mock_github_state.build_transport(),
    )
    async with app.state.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
            await connect_owner_github_account(c)
            await c.post(
                f"/api/v1/projects/{PROJECT_ID}/github/connect",
                json={"github_repository_id": 1001, "full_name": "alice-gh/sample-api"},
                headers=auth_headers(TEST_OWNER_TOKEN),
            )

            huge_resp = await c.get(
                f"/api/v1/projects/{PROJECT_ID}/github/contents",
                params={"path": "huge.txt"},
                headers=auth_headers(TEST_OWNER_TOKEN),
            )
            assert huge_resp.status_code == 200
            assert huge_resp.json()["truncated"] is True
            assert len(huge_resp.json()["content"]) == 1024

            many_resp = await c.get(
                f"/api/v1/projects/{PROJECT_ID}/github/contents",
                params={"path": "many"},
                headers=auth_headers(TEST_OWNER_TOKEN),
            )
            assert many_resp.status_code == 200
            assert many_resp.json()["truncated"] is True
            assert len(many_resp.json()["entries"]) == 2


@pytest.mark.asyncio
async def test_snapshot_archive_access_control_and_redirect_safety(
    tmp_path,
    mock_identity_state: MockIdentityState,
    mock_project_state: MockProjectState,
    mock_github_state: MockGitHubState,
) -> None:
    import io
    import zipfile

    from tests.conftest import (
        ASSIGNED_PROJECT_ID,
        TEST_OTHER_FREELANCER_TOKEN,
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("alice-gh-sample-api-abc123/README.md", "# Snapshot\n")
    valid_zip = buf.getvalue()
    mock_github_state.snapshot_zip_bytes = valid_zip
    mock_github_state.snapshot_redirect_url = "https://codeload.github.com/alice-gh/sample-api/zip/main"

    db_file = (tmp_path / "snapshot.db").as_posix()
    settings = build_test_settings(db_file)
    app = create_app(
        settings=settings,
        identity_transport=mock_identity_state.build_transport(),
        project_transport=mock_project_state.build_transport(),
        github_transport=mock_github_state.build_transport(),
    )
    async with app.state.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
            await connect_owner_github_account(c)
            await c.post(
                f"/api/v1/projects/{ASSIGNED_PROJECT_ID}/github/connect",
                json={"github_repository_id": 1001, "full_name": "alice-gh/sample-api"},
                headers=auth_headers(TEST_OWNER_TOKEN),
            )

            # Owner can download snapshot through safe codeload redirect
            owner_snap = await c.get(
                f"/api/v1/projects/{ASSIGNED_PROJECT_ID}/github/snapshot",
                headers=auth_headers(TEST_OWNER_TOKEN),
            )
            assert owner_snap.status_code == 200
            assert owner_snap.headers["content-type"].startswith("application/zip")
            assert owner_snap.content == valid_zip

            # Actively assigned freelancer can download snapshot without seeing OAuth token
            fl_snap = await c.get(
                f"/api/v1/projects/{ASSIGNED_PROJECT_ID}/github/snapshot",
                headers=auth_headers(TEST_FREELANCER_TOKEN),
            )
            assert fl_snap.status_code == 200
            assert fl_snap.content == valid_zip

            # Other freelancer, admin, and other owner are forbidden
            for forbidden_tok in (TEST_OTHER_FREELANCER_TOKEN, TEST_OTHER_OWNER_TOKEN, TEST_ADMIN_TOKEN):
                denied = await c.get(
                    f"/api/v1/projects/{ASSIGNED_PROJECT_ID}/github/snapshot",
                    headers=auth_headers(forbidden_tok),
                )
                assert denied.status_code == 403

            # Reject unsafe external redirect host
            mock_github_state.snapshot_redirect_url = "https://evil.example.com/steal.zip"
            bad_redir = await c.get(
                f"/api/v1/projects/{ASSIGNED_PROJECT_ID}/github/snapshot",
                headers=auth_headers(TEST_OWNER_TOKEN),
            )
            assert bad_redir.status_code == 502

