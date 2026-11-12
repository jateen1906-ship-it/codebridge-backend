"""GitHubServiceClient unit tests verifying strict internal communication and error mapping."""

from __future__ import annotations

import httpx
import pytest

from app.clients.github_client import GitHubServiceClient
from app.core.exceptions import (
    BadRequestError,
    NotFoundError,
    UpstreamRateLimitError,
    UpstreamServiceUnavailableError,
)
from tests.conftest import (
    DISCONNECTED_PROJECT_ID,
    OWNER_TOKEN,
    PROJECT_ID,
    FakeGitHubRepoState,
    build_sample_repo_files,
    make_github_service_transport,
)


@pytest.mark.asyncio
async def test_github_client_reads_only_via_github_service_endpoints(
    base_test_settings,
) -> None:
    seen_hosts: list[str] = []
    repo_state = FakeGitHubRepoState(files=build_sample_repo_files())
    inner_transport = make_github_service_transport({str(PROJECT_ID): repo_state})

    def spy_handler(request: httpx.Request) -> httpx.Response:
        seen_hosts.append(str(request.url.host))
        return inner_transport.handle_request(request)

    client = GitHubServiceClient(base_test_settings, transport=httpx.MockTransport(spy_handler))
    await client.startup()
    try:
        status = await client.get_project_repository_status(
            PROJECT_ID,
            bearer_token=OWNER_TOKEN,
            request_id="req-1",
        )
        assert status.full_name == "owner/sample-payment-app"
        branches = await client.list_branches(
            PROJECT_ID,
            bearer_token=OWNER_TOKEN,
            request_id="req-1",
        )
        assert any(b.name == "main" for b in branches)
        root_dir = await client.get_contents(
            PROJECT_ID,
            bearer_token=OWNER_TOKEN,
            request_id="req-1",
            path="",
            ref="main",
        )
        assert root_dir.kind == "dir"
        assert all(host == "github.test" for host in seen_hosts)
        assert not any("github.com" in host for host in seen_hosts)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_github_client_maps_disconnected_rate_limit_and_unavailable_errors(
    base_test_settings,
) -> None:
    repo_state = FakeGitHubRepoState(files=build_sample_repo_files(), rate_limit_contents=True)
    transport = make_github_service_transport(
        {
            str(PROJECT_ID): repo_state,
            str(DISCONNECTED_PROJECT_ID): FakeGitHubRepoState(connected=False),
        }
    )
    client = GitHubServiceClient(base_test_settings, transport=transport)
    await client.startup()
    try:
        with pytest.raises(NotFoundError) as exc_disc:
            await client.get_project_repository_status(
                DISCONNECTED_PROJECT_ID,
                bearer_token=OWNER_TOKEN,
                request_id="req-2",
            )
        assert exc_disc.value.error_code == "REPOSITORY_NOT_CONNECTED"

        with pytest.raises(UpstreamRateLimitError):
            await client.get_contents(
                PROJECT_ID,
                bearer_token=OWNER_TOKEN,
                request_id="req-2",
                path="",
            )

        with pytest.raises(BadRequestError):
            await client.get_contents(
                PROJECT_ID,
                bearer_token=OWNER_TOKEN,
                request_id="req-2",
                path="../etc/passwd",
            )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_github_client_502_maps_to_github_service_unavailable(
    base_test_settings,
) -> None:
    transport = httpx.MockTransport(lambda _req: httpx.Response(502, json={"detail": "Bad gateway"}))
    client = GitHubServiceClient(base_test_settings, transport=transport)
    await client.startup()
    try:
        with pytest.raises(UpstreamServiceUnavailableError) as exc:
            await client.get_project_repository_status(
                PROJECT_ID,
                bearer_token=OWNER_TOKEN,
                request_id="req-3",
            )
        assert exc.value.error_code == "GITHUB_SERVICE_UNAVAILABLE"
    finally:
        await client.close()

