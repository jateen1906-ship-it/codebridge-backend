"""Tests for GET /api/v1/github/oauth/callback code exchange and encrypted persistence."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import TokenEncryptionService
from app.main import create_app
from app.models.base import Base
from app.models.github_connection import GitHubConnection, GitHubConnectionStatus
from app.models.oauth_state import OAuthState
from tests.conftest import (
    OWNER_ID,
    PROJECT_ID,
    SECOND_PROJECT_ID,
    TEST_ENCRYPTION_KEY,
    TEST_OWNER_TOKEN,
    MockGitHubState,
    MockIdentityState,
    MockProjectState,
    auth_headers,
    build_test_settings,
)


def _extract_state(location: str) -> str:
    return parse_qs(urlparse(location).query)["state"][0]


@pytest.mark.asyncio
async def test_callback_exchanges_code_encrypts_token_and_returns_safe_metadata(
    client: AsyncClient,
    db_session: AsyncSession,
    mock_github_state: MockGitHubState,
) -> None:
    auth_resp = await client.get(
        "/api/v1/github/oauth/authorize",
        params={"project_id": str(PROJECT_ID)},
        headers=auth_headers(TEST_OWNER_TOKEN),
        follow_redirects=False,
    )
    raw_state = _extract_state(auth_resp.headers["location"])

    cb_resp = await client.get(
        "/api/v1/github/oauth/callback",
        params={"code": "valid-code", "state": raw_state},
    )
    assert cb_resp.status_code == 200
    assert cb_resp.headers.get("cache-control") == "no-store"
    body = cb_resp.json()
    assert body["success"] is True
    assert body["status"] == "CONNECTED"
    assert body["project_id"] == str(PROJECT_ID)
    assert body["github_user_id"] == 424242
    assert body["github_login"] == "alice-gh"
    assert body["scopes"] == ["repo", "read:user"]
    assert "gho_super_secret_oauth_token_123456" not in cb_resp.text
    assert "test-client-secret" not in cb_resp.text
    assert raw_state not in cb_resp.text

    conns = (await db_session.execute(select(GitHubConnection))).scalars().all()
    assert len(conns) == 1
    conn = conns[0]
    assert conn.owner_id == OWNER_ID
    assert conn.status == GitHubConnectionStatus.ACTIVE
    assert conn.encrypted_access_token.startswith("v1:")
    assert "gho_super_secret_oauth_token_123456" not in conn.encrypted_access_token

    cipher = TokenEncryptionService(
        __import__("base64").urlsafe_b64decode(TEST_ENCRYPTION_KEY.encode("ascii"))
    )
    assert (
        cipher.decrypt(conn.encrypted_access_token, owner_id=OWNER_ID)
        == "gho_super_secret_oauth_token_123456"
    )

    state_rows = (await db_session.execute(select(OAuthState))).scalars().all()
    assert len(state_rows) == 1
    assert state_rows[0].consumed_at is not None
    assert mock_github_state.token_exchange_calls == 1


@pytest.mark.asyncio
async def test_callback_refreshes_same_github_account_and_conflicts_on_different_account(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    # First connection
    auth1 = await client.get(
        "/api/v1/github/oauth/authorize",
        params={"project_id": str(PROJECT_ID)},
        headers=auth_headers(TEST_OWNER_TOKEN),
        follow_redirects=False,
    )
    cb1 = await client.get(
        "/api/v1/github/oauth/callback",
        params={"code": "valid-code", "state": _extract_state(auth1.headers["location"])},
    )
    assert cb1.status_code == 200
    first_conn_id = cb1.json()["connection_id"]

    # Same GitHub user re-authorizes -> updates existing connection cleanly
    auth2 = await client.get(
        "/api/v1/github/oauth/authorize",
        params={"project_id": str(SECOND_PROJECT_ID)},
        headers=auth_headers(TEST_OWNER_TOKEN),
        follow_redirects=False,
    )
    cb2 = await client.get(
        "/api/v1/github/oauth/callback",
        params={"code": "second-code", "state": _extract_state(auth2.headers["location"])},
    )
    assert cb2.status_code == 200
    assert cb2.json()["connection_id"] == first_conn_id
    assert cb2.json()["github_login"] == "alice-gh-renamed"

    # Different GitHub user while ACTIVE -> 409 GITHUB_CONNECTION_ALREADY_EXISTS
    auth3 = await client.get(
        "/api/v1/github/oauth/authorize",
        params={"project_id": str(PROJECT_ID)},
        headers=auth_headers(TEST_OWNER_TOKEN),
        follow_redirects=False,
    )
    cb3 = await client.get(
        "/api/v1/github/oauth/callback",
        params={"code": "other-gh-user-code", "state": _extract_state(auth3.headers["location"])},
    )
    assert cb3.status_code == 409
    assert cb3.json()["error"]["code"] == "GITHUB_CONNECTION_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_callback_handles_oauth_denial_upstream_error_and_optional_redirect(
    tmp_path,
    mock_identity_state: MockIdentityState,
    mock_project_state: MockProjectState,
    mock_github_state: MockGitHubState,
    client: AsyncClient,
) -> None:
    # GitHub denied authorization
    denied = await client.get(
        "/api/v1/github/oauth/callback",
        params={"error": "access_denied", "state": "unused"},
    )
    assert denied.status_code == 400
    assert denied.json()["error"]["code"] == "GITHUB_OAUTH_DENIED"

    # GitHub token exchange fails upstream
    auth_resp = await client.get(
        "/api/v1/github/oauth/authorize",
        params={"project_id": str(PROJECT_ID)},
        headers=auth_headers(TEST_OWNER_TOKEN),
        follow_redirects=False,
    )
    raw_state = _extract_state(auth_resp.headers["location"])
    mock_github_state.forced_token_status = 500
    failed = await client.get(
        "/api/v1/github/oauth/callback",
        params={"code": "valid-code", "state": raw_state},
    )
    assert failed.status_code == 502
    assert failed.json()["error"]["code"] == "GITHUB_OAUTH_FAILED"
    mock_github_state.forced_token_status = None

    # Configured FRONTEND_OAUTH_SUCCESS_URL returns non-secret 302 redirect
    redirect_db = (tmp_path / "redirect_test.db").as_posix()
    settings = build_test_settings(
        redirect_db,
        FRONTEND_OAUTH_SUCCESS_URL="http://localhost:3000/github/connected",
    )
    app = create_app(
        settings=settings,
        identity_transport=mock_identity_state.build_transport(),
        project_transport=mock_project_state.build_transport(),
        github_transport=mock_github_state.build_transport(),
    )
    async with app.state.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as rc:
            a_resp = await rc.get(
                "/api/v1/github/oauth/authorize",
                params={"project_id": str(PROJECT_ID)},
                headers=auth_headers(TEST_OWNER_TOKEN),
                follow_redirects=False,
            )
            st = _extract_state(a_resp.headers["location"])
            cb_redir = await rc.get(
                "/api/v1/github/oauth/callback",
                params={"code": "valid-code", "state": st},
                follow_redirects=False,
            )
            assert cb_redir.status_code == 302
            loc = cb_redir.headers["location"]
            assert loc.startswith("http://localhost:3000/github/connected?")
            assert "gho_super_secret" not in loc
            assert "code=" not in loc
            assert "state=" not in loc
            assert "status=connected" in loc
