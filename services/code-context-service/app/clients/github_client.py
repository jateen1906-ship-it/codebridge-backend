"""Synchronous HTTP/REST client for GitHub Service — the sole permitted repo source."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError as PydanticValidationError

from app.core.exceptions import (
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    UpstreamRateLimitError,
    UpstreamServiceUnavailableError,
)
from app.core.logging import get_logger
from app.core.security import REQUEST_ID_HEADER, validate_safe_repo_path

if TYPE_CHECKING:
    from app.core.config import Settings

logger = get_logger("clients.github")

_STATUS_TEMPLATE = "/api/v1/projects/{project_id}/github/status"
_BRANCHES_TEMPLATE = "/api/v1/projects/{project_id}/github/branches"
_CONTENTS_TEMPLATE = "/api/v1/projects/{project_id}/github/contents"


class _GitHubRepoMetadataPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    github_repository_id: int
    full_name: str
    default_branch: str


class _GitHubStatusPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    project_id: uuid.UUID
    connected: bool
    connection_status: str
    repository: _GitHubRepoMetadataPayload | None = None


class _BranchItemPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    commit_sha: str
    protected: bool = False


class _BranchListPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    project_id: uuid.UUID
    full_name: str
    items: list[_BranchItemPayload]
    page: int = 1
    per_page: int = 30
    has_next_page: bool = False


class _ContentEntryPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    path: str
    type: Literal["file", "dir", "symlink", "submodule", "other"]
    size: int = 0
    sha: str


class _ContentsPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    project_id: uuid.UUID
    full_name: str
    ref: str | None = None
    path: str
    kind: Literal["file", "dir"]
    sha: str | None = None
    size: int | None = None
    encoding: str | None = None
    is_binary: bool = False
    truncated: bool = False
    content: str | None = None
    entries: list[_ContentEntryPayload] | None = None


@dataclass(frozen=True, slots=True)
class ConnectedRepositoryInfo:
    project_id: uuid.UUID
    github_repository_id: int
    full_name: str
    default_branch: str


@dataclass(frozen=True, slots=True)
class GitHubBranchInfo:
    name: str
    commit_sha: str
    protected: bool = False


@dataclass(frozen=True, slots=True)
class GitHubDirectoryEntry:
    name: str
    path: str
    entry_type: str
    size: int
    sha: str


@dataclass(frozen=True, slots=True)
class GitHubContentNode:
    project_id: uuid.UUID
    full_name: str
    ref: str | None
    path: str
    kind: Literal["file", "dir"]
    sha: str | None = None
    size: int | None = None
    encoding: str | None = None
    is_binary: bool = False
    truncated: bool = False
    content: str | None = None
    entries: tuple[GitHubDirectoryEntry, ...] = ()


class GitHubServiceClient:
    """Reusable HTTPX client communicating ONLY with internal GitHub Service endpoints."""

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = settings.GITHUB_SERVICE_URL.rstrip("/")
        self._timeout_seconds = settings.SERVICE_CLIENT_TIMEOUT_SECONDS
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    @property
    def is_open(self) -> bool:
        return self._client is not None and not self._client.is_closed

    async def startup(self) -> None:
        """Initialize pooled HTTPX AsyncClient."""
        if self._client is None or self._client.is_closed:
            timeout = httpx.Timeout(
                timeout=self._timeout_seconds,
                connect=min(2.0, self._timeout_seconds),
            )
            limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)
            client_kwargs: dict[str, object] = {
                "base_url": self._base_url,
                "timeout": timeout,
                "limits": limits,
                "follow_redirects": False,
            }
            if self._transport is not None:
                client_kwargs["transport"] = self._transport
            self._client = httpx.AsyncClient(**client_kwargs)  # type: ignore[arg-type]

    async def close(self) -> None:
        """Close the HTTPX AsyncClient cleanly."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            raise UpstreamServiceUnavailableError(
                "GitHub service is unavailable.",
                error_code="GITHUB_SERVICE_UNAVAILABLE",
            )
        return self._client

    def _map_upstream_error(
        self,
        response: httpx.Response,
        *,
        project_id: str,
        operation: str,
        path: str | None = None,
    ) -> Exception:
        status_code = response.status_code
        if status_code == 400:
            return BadRequestError(
                "Invalid repository path or branch reference.",
                error_code="VALIDATION_ERROR",
            )
        if status_code == 401:
            return UnauthorizedError("Invalid or expired authentication token.")
        if status_code == 403:
            return ForbiddenError("Not authorized to access the connected GitHub repository.")
        if status_code == 404:
            # GitHub Service returns 404 for project not found, repository not connected,
            # or branch/path not found. Distinguish repository-not-connected vs path miss.
            error_code = "INDEX_NOT_FOUND" if operation == "contents" and path else "REPOSITORY_NOT_CONNECTED"
            try:
                body = response.json()
                upstream_code = (
                    body.get("error", {}).get("code")
                    if isinstance(body, dict)
                    else None
                )
                if upstream_code == "PROJECT_NOT_FOUND":
                    error_code = "PROJECT_NOT_FOUND"
                elif upstream_code == "REPOSITORY_NOT_CONNECTED":
                    error_code = "REPOSITORY_NOT_CONNECTED"
            except ValueError:
                pass
            return NotFoundError(
                "Connected repository resource was not found.",
                error_code=error_code,
            )
        if status_code == 429:
            return UpstreamRateLimitError(
                "GitHub rate limit reached while reading repository.",
                error_code="GITHUB_SERVICE_UNAVAILABLE",
            )
        logger.warning(
            "GitHub Service returned unexpected status",
            extra={
                "event": "github_service_unexpected_status",
                "target_service": "github-service",
                "project_id": project_id,
                "status_code": status_code,
                "error_code": "GITHUB_SERVICE_UNAVAILABLE",
            },
        )
        return UpstreamServiceUnavailableError(
            "GitHub service is temporarily unavailable.",
            error_code="GITHUB_SERVICE_UNAVAILABLE",
        )

    async def _request(
        self,
        url_path: str,
        *,
        bearer_token: str,
        request_id: str,
        project_id: str,
        operation: str,
        params: Mapping[str, str | int | float | bool | None] | None = None,
        repo_path: str | None = None,
    ) -> httpx.Response:
        client = self._require_client()
        try:
            response = await client.get(
                url_path,
                params=params,
                headers={
                    "Authorization": f"Bearer {bearer_token}",
                    REQUEST_ID_HEADER: request_id,
                    "Accept": "application/json",
                },
            )
        except httpx.TimeoutException as exc:
            logger.warning(
                "GitHub Service request timed out",
                extra={
                    "event": "github_service_timeout",
                    "target_service": "github-service",
                    "project_id": project_id,
                    "error_code": "GITHUB_SERVICE_UNAVAILABLE",
                },
            )
            raise UpstreamServiceUnavailableError(
                "GitHub service request timed out.",
                error_code="GITHUB_SERVICE_UNAVAILABLE",
            ) from exc
        except httpx.RequestError as exc:
            logger.warning(
                "Failed to connect to GitHub Service",
                extra={
                    "event": "github_service_connect_error",
                    "target_service": "github-service",
                    "project_id": project_id,
                    "error_code": "GITHUB_SERVICE_UNAVAILABLE",
                },
            )
            raise UpstreamServiceUnavailableError(
                "GitHub service is temporarily unavailable.",
                error_code="GITHUB_SERVICE_UNAVAILABLE",
            ) from exc

        if response.status_code != 200:
            raise self._map_upstream_error(
                response,
                project_id=project_id,
                operation=operation,
                path=repo_path,
            )
        return response

    async def get_project_repository_status(
        self,
        project_id: uuid.UUID,
        *,
        bearer_token: str,
        request_id: str,
    ) -> ConnectedRepositoryInfo:
        """Fetch active connected repository metadata via GitHub Service."""
        canonical_id = str(uuid.UUID(str(project_id)))
        response = await self._request(
            _STATUS_TEMPLATE.format(project_id=canonical_id),
            bearer_token=bearer_token,
            request_id=request_id,
            project_id=canonical_id,
            operation="status",
        )
        try:
            parsed = _GitHubStatusPayload.model_validate(response.json())
        except (ValueError, PydanticValidationError) as exc:
            raise UpstreamServiceUnavailableError(
                "GitHub service returned an invalid repository status response.",
                error_code="GITHUB_SERVICE_UNAVAILABLE",
            ) from exc

        if (
            not parsed.connected
            or parsed.connection_status.upper() != "ACTIVE"
            or parsed.repository is None
        ):
            raise NotFoundError(
                "No active GitHub repository is connected to this project.",
                error_code="REPOSITORY_NOT_CONNECTED",
            )

        return ConnectedRepositoryInfo(
            project_id=parsed.project_id,
            github_repository_id=parsed.repository.github_repository_id,
            full_name=parsed.repository.full_name,
            default_branch=parsed.repository.default_branch,
        )

    async def list_branches(
        self,
        project_id: uuid.UUID,
        *,
        bearer_token: str,
        request_id: str,
        page: int = 1,
        per_page: int = 50,
    ) -> list[GitHubBranchInfo]:
        """List repository branches via GitHub Service."""
        canonical_id = str(uuid.UUID(str(project_id)))
        response = await self._request(
            _BRANCHES_TEMPLATE.format(project_id=canonical_id),
            bearer_token=bearer_token,
            request_id=request_id,
            project_id=canonical_id,
            operation="branches",
            params={"page": page, "per_page": per_page},
        )
        try:
            parsed = _BranchListPayload.model_validate(response.json())
        except (ValueError, PydanticValidationError) as exc:
            raise UpstreamServiceUnavailableError(
                "GitHub service returned an invalid branch listing response.",
                error_code="GITHUB_SERVICE_UNAVAILABLE",
            ) from exc

        return [
            GitHubBranchInfo(name=item.name, commit_sha=item.commit_sha, protected=item.protected)
            for item in parsed.items
        ]

    async def get_contents(
        self,
        project_id: uuid.UUID,
        *,
        bearer_token: str,
        request_id: str,
        path: str = "",
        ref: str | None = None,
    ) -> GitHubContentNode:
        """Read bounded directory entries or decoded file text via GitHub Service."""
        safe_path = validate_safe_repo_path(path)
        canonical_id = str(uuid.UUID(str(project_id)))
        params: dict[str, str] = {"path": safe_path}
        if ref is not None:
            params["ref"] = ref

        response = await self._request(
            _CONTENTS_TEMPLATE.format(project_id=canonical_id),
            bearer_token=bearer_token,
            request_id=request_id,
            project_id=canonical_id,
            operation="contents",
            params=params,
            repo_path=safe_path,
        )
        try:
            parsed = _ContentsPayload.model_validate(response.json())
        except (ValueError, PydanticValidationError) as exc:
            raise UpstreamServiceUnavailableError(
                "GitHub service returned an invalid repository contents response.",
                error_code="GITHUB_SERVICE_UNAVAILABLE",
            ) from exc

        entries = tuple(
            GitHubDirectoryEntry(
                name=entry.name,
                path=entry.path,
                entry_type=entry.type,
                size=entry.size,
                sha=entry.sha,
            )
            for entry in (parsed.entries or [])
        )
        return GitHubContentNode(
            project_id=parsed.project_id,
            full_name=parsed.full_name,
            ref=parsed.ref,
            path=parsed.path,
            kind=parsed.kind,
            sha=parsed.sha,
            size=parsed.size,
            encoding=parsed.encoding,
            is_binary=parsed.is_binary,
            truncated=parsed.truncated,
            content=parsed.content,
            entries=entries,
        )

