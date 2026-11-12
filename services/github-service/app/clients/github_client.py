"""Fixed-host HTTPS GitHub OAuth and REST API client (anti-SSRF, read-only repo access)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlencode

import httpx

from app.core.exceptions import (
    GitHubRateLimitError,
    GitHubUpstreamError,
    NotFoundError,
    UpstreamTimeoutError,
)
from app.core.logging import get_logger
from app.core.security import validate_repo_full_name

if TYPE_CHECKING:
    from app.core.config import Settings

logger = get_logger("clients.github")

GITHUB_OAUTH_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_OAUTH_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API_BASE_URL = "https://api.github.com"
_GITHUB_API_VERSION = "2022-11-28"
_USER_AGENT = "CodeBridge-GitHub-Service/1.0"
_MAX_RAW_RESPONSE_BYTES = 1_048_576  # 1 MiB transport safety ceiling


class GitHubHTTPClient:
    """Lifespan-managed HTTPX client communicating exclusively with fixed GitHub endpoints."""

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    @property
    def is_open(self) -> bool:
        return self._client is not None and not self._client.is_closed

    async def startup(self) -> None:
        """Initialize the pooled HTTPX AsyncClient against fixed https://api.github.com."""
        if self._client is None or self._client.is_closed:
            timeout = httpx.Timeout(
                timeout=self._settings.GITHUB_HTTP_TIMEOUT_SECONDS,
                connect=min(3.0, self._settings.GITHUB_HTTP_TIMEOUT_SECONDS),
            )
            limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)
            client_kwargs: dict[str, object] = {
                "base_url": GITHUB_API_BASE_URL,
                "timeout": timeout,
                "limits": limits,
                "follow_redirects": False,
            }
            if self._transport is not None:
                client_kwargs["transport"] = self._transport
            self._client = httpx.AsyncClient(**client_kwargs)  # type: ignore[arg-type]

    async def close(self) -> None:
        """Close the HTTPX AsyncClient cleanly on shutdown."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            raise GitHubUpstreamError("GitHub client is not initialized.")
        return self._client

    def build_authorize_url(self, *, state: str) -> str:
        """Construct the fixed GitHub OAuth authorization redirect URL (no client_secret)."""
        query = urlencode(
            {
                "client_id": self._settings.GITHUB_CLIENT_ID,
                "redirect_uri": self._settings.GITHUB_OAUTH_CALLBACK_URL,
                "scope": self._settings.GITHUB_OAUTH_SCOPES,
                "state": state,
            }
        )
        return f"{GITHUB_OAUTH_AUTHORIZE_URL}?{query}"

    async def exchange_code_for_token(self, *, code: str) -> tuple[str, str]:
        """Exchange an OAuth authorization code with https://github.com/login/oauth/access_token."""
        client = self._require_client()
        try:
            response = await client.post(
                GITHUB_OAUTH_TOKEN_URL,
                headers={
                    "Accept": "application/json",
                    "User-Agent": _USER_AGENT,
                },
                data={
                    "client_id": self._settings.GITHUB_CLIENT_ID,
                    "client_secret": self._settings.GITHUB_CLIENT_SECRET,
                    "code": code,
                    "redirect_uri": self._settings.GITHUB_OAUTH_CALLBACK_URL,
                },
            )
        except httpx.TimeoutException as exc:
            logger.warning(
                "GitHub OAuth token exchange timed out",
                extra={
                    "event": "github_oauth_timeout",
                    "target_service": "github",
                    "error_code": "UPSTREAM_TIMEOUT",
                },
            )
            raise UpstreamTimeoutError("GitHub OAuth token exchange timed out.") from exc
        except httpx.RequestError as exc:
            logger.warning(
                "GitHub OAuth token exchange failed due to transport error",
                extra={
                    "event": "github_oauth_transport_error",
                    "target_service": "github",
                    "error_code": "GITHUB_OAUTH_FAILED",
                },
            )
            raise GitHubUpstreamError(
                "Unable to complete OAuth token exchange with GitHub.",
                error_code="GITHUB_OAUTH_FAILED",
            ) from exc

        if response.status_code != 200:
            raise GitHubUpstreamError(
                "GitHub OAuth token exchange failed.",
                error_code="GITHUB_OAUTH_FAILED",
            )

        try:
            payload: Any = response.json()
        except ValueError as exc:
            raise GitHubUpstreamError(
                "GitHub OAuth returned a malformed response.",
                error_code="GITHUB_OAUTH_FAILED",
            ) from exc

        if not isinstance(payload, dict) or payload.get("error"):
            raise GitHubUpstreamError(
                "GitHub rejected the OAuth authorization code.",
                error_code="GITHUB_OAUTH_FAILED",
            )

        access_token = payload.get("access_token")
        scope_str = payload.get("scope")
        if not isinstance(access_token, str) or not access_token.strip():
            raise GitHubUpstreamError(
                "GitHub OAuth response omitted an access token.",
                error_code="GITHUB_OAUTH_FAILED",
            )

        resolved_scope = (
            scope_str.strip()
            if isinstance(scope_str, str) and scope_str.strip()
            else self._settings.GITHUB_OAUTH_SCOPES
        )
        return access_token.strip(), resolved_scope

    async def get_authenticated_user(self, *, access_token: str) -> dict[str, Any]:
        """Fetch GET https://api.github.com/user using an encrypted/decrypted OAuth token."""
        data, _headers = await self._api_get("/user", access_token=access_token)
        if not isinstance(data, dict):
            raise GitHubUpstreamError("GitHub user response had unexpected shape.")
        return data

    async def list_repositories(
        self,
        *,
        access_token: str,
        page: int,
        per_page: int,
        visibility: str = "all",
    ) -> tuple[list[dict[str, Any]], bool]:
        """Fetch paginated accessible repositories from GET https://api.github.com/user/repos."""
        data, headers = await self._api_get(
            "/user/repos",
            access_token=access_token,
            params={
                "sort": "updated",
                "direction": "desc",
                "affiliation": "owner,collaborator,organization_member",
                "visibility": visibility,
                "per_page": str(per_page),
                "page": str(page),
            },
        )
        if not isinstance(data, list):
            raise GitHubUpstreamError("GitHub repository list response had unexpected shape.")
        has_next = _has_next_page(headers.get("link"))
        return [item for item in data if isinstance(item, dict)], has_next

    async def get_repository(
        self,
        *,
        access_token: str,
        full_name: str,
    ) -> dict[str, Any]:
        """Fetch repository metadata from fixed GET /repos/{owner}/{repo}."""
        clean_full_name = validate_repo_full_name(full_name)
        owner_seg, repo_seg = clean_full_name.split("/", 1)
        path = f"/repos/{quote(owner_seg, safe='')}/{quote(repo_seg, safe='')}"
        data, _headers = await self._api_get(path, access_token=access_token)
        if not isinstance(data, dict):
            raise GitHubUpstreamError("GitHub repository response had unexpected shape.")
        return data

    async def list_branches(
        self,
        *,
        access_token: str,
        full_name: str,
        page: int,
        per_page: int,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Fetch read-only branches list from GET /repos/{owner}/{repo}/branches."""
        clean_full_name = validate_repo_full_name(full_name)
        owner_seg, repo_seg = clean_full_name.split("/", 1)
        path = f"/repos/{quote(owner_seg, safe='')}/{quote(repo_seg, safe='')}/branches"
        data, headers = await self._api_get(
            path,
            access_token=access_token,
            params={"page": str(page), "per_page": str(per_page)},
        )
        if not isinstance(data, list):
            raise GitHubUpstreamError("GitHub branches response had unexpected shape.")
        has_next = _has_next_page(headers.get("link"))
        return [item for item in data if isinstance(item, dict)], has_next

    async def get_contents(
        self,
        *,
        access_token: str,
        full_name: str,
        repo_path: str = "",
        ref: str | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Fetch read-only file/directory contents from GET /repos/{owner}/{repo}/contents/{path}."""
        clean_full_name = validate_repo_full_name(full_name)
        owner_seg, repo_seg = clean_full_name.split("/", 1)
        base = f"/repos/{quote(owner_seg, safe='')}/{quote(repo_seg, safe='')}/contents"
        if repo_path:
            encoded_segments = "/".join(quote(seg, safe="") for seg in repo_path.split("/"))
            path = f"{base}/{encoded_segments}"
        else:
            path = base

        params = {"ref": ref} if ref else None
        data, _headers = await self._api_get(path, access_token=access_token, params=params)
        if not isinstance(data, (dict, list)):
            raise GitHubUpstreamError("GitHub contents response had unexpected shape.")
        return data

    async def list_commits(
        self,
        *,
        access_token: str,
        full_name: str,
        page: int,
        per_page: int,
        sha: str | None = None,
        repo_path: str | None = None,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Fetch read-only commit history from GET /repos/{owner}/{repo}/commits."""
        clean_full_name = validate_repo_full_name(full_name)
        owner_seg, repo_seg = clean_full_name.split("/", 1)
        path = f"/repos/{quote(owner_seg, safe='')}/{quote(repo_seg, safe='')}/commits"
        params: dict[str, str] = {"page": str(page), "per_page": str(per_page)}
        if sha:
            params["sha"] = sha
        if repo_path:
            params["path"] = repo_path

        data, headers = await self._api_get(path, access_token=access_token, params=params)
        if not isinstance(data, list):
            raise GitHubUpstreamError("GitHub commits response had unexpected shape.")
        has_next = _has_next_page(headers.get("link"))
        return [item for item in data if isinstance(item, dict)], has_next

    async def _api_get(
        self,
        path: str,
        *,
        access_token: str,
        params: dict[str, str] | None = None,
    ) -> tuple[Any, httpx.Headers]:
        """Execute a strictly read-only GET against fixed https://api.github.com."""
        if not path.startswith("/"):
            raise ValueError("Internal GitHub API path must be relative starting with '/'.")
        client = self._require_client()
        try:
            response = await client.get(
                path,
                params=params,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": _GITHUB_API_VERSION,
                    "User-Agent": _USER_AGENT,
                },
            )
        except httpx.TimeoutException as exc:
            logger.warning(
                "GitHub API request timed out",
                extra={
                    "event": "github_api_timeout",
                    "target_service": "github",
                    "path": path,
                    "error_code": "UPSTREAM_TIMEOUT",
                },
            )
            raise UpstreamTimeoutError("GitHub API request timed out.") from exc
        except httpx.RequestError as exc:
            logger.warning(
                "GitHub API transport failure",
                extra={
                    "event": "github_api_transport_error",
                    "target_service": "github",
                    "path": path,
                    "error_code": "GITHUB_UPSTREAM_ERROR",
                },
            )
            raise GitHubUpstreamError("Unable to communicate with GitHub API.") from exc

        if response.status_code == 429 or _is_rate_limited_response(response):
            raise GitHubRateLimitError()
        if response.status_code == 401:
            raise GitHubUpstreamError(
                "Stored GitHub OAuth credential is invalid or revoked.",
                error_code="GITHUB_TOKEN_INVALID",
            )
        if response.status_code == 403:
            raise GitHubUpstreamError(
                "GitHub denied permission for the requested resource.",
                error_code="GITHUB_ACCESS_DENIED",
            )
        if response.status_code == 404:
            raise NotFoundError(
                "Requested GitHub resource was not found or is inaccessible.",
                error_code="GITHUB_RESOURCE_NOT_FOUND",
            )
        if response.status_code >= 400:
            raise GitHubUpstreamError(
                "GitHub API failed to fulfill the request.",
                error_code="GITHUB_UPSTREAM_ERROR",
            )

        if len(response.content) > _MAX_RAW_RESPONSE_BYTES:
            raise GitHubUpstreamError(
                "GitHub API response exceeded maximum allowed payload size.",
                error_code="GITHUB_UPSTREAM_ERROR",
            )

        try:
            return response.json(), response.headers
        except ValueError as exc:
            raise GitHubUpstreamError(
                "GitHub API returned malformed JSON.",
                error_code="GITHUB_UPSTREAM_ERROR",
            ) from exc


def _is_rate_limited_response(response: httpx.Response) -> bool:
    if response.status_code != 403:
        return False
    remaining = response.headers.get("x-ratelimit-remaining")
    if remaining == "0":
        return True
    try:
        data = response.json()
        if isinstance(data, dict) and isinstance(data.get("message"), str):
            return "rate limit" in data["message"].lower()
    except ValueError:
        pass
    return False


def _has_next_page(link_header: str | None) -> bool:
    if not link_header:
        return False
    return 'rel="next"' in link_header

