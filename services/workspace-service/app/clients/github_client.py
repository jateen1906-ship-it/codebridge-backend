"""Synchronous HTTP/REST client consuming repository snapshots exclusively via GitHub Service."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError as PydanticValidationError

from app.core.exceptions import (
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    UpstreamServiceUnavailableError,
)
from app.core.logging import get_logger
from app.core.security import REQUEST_ID_HEADER

if TYPE_CHECKING:
    from app.core.config import Settings

logger = get_logger("clients.github")

_GITHUB_STATUS_TEMPLATE = "/api/v1/projects/{project_id}/github/status"
_GITHUB_SNAPSHOT_TEMPLATE = "/api/v1/projects/{project_id}/github/snapshot"


class _RepositoryMetadataPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    full_name: str
    default_branch: str = "main"


class _GitHubStatusPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    project_id: uuid.UUID
    connected: bool
    repository: _RepositoryMetadataPayload | None = None


@dataclass(frozen=True, slots=True)
class ConnectedRepositoryInfo:
    project_id: uuid.UUID
    full_name: str
    default_branch: str


@dataclass(frozen=True, slots=True)
class RepositorySnapshotBundle:
    full_name: str
    ref: str
    archive_bytes: bytes


class GitHubServiceClient:
    """HTTPX client communicating exclusively with CodeBridge GitHub Service (never api.github.com)."""

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = settings.GITHUB_SERVICE_URL.rstrip("/")
        self._timeout_seconds = settings.SERVICE_CLIENT_TIMEOUT_SECONDS
        self._max_archive_bytes = settings.WORKSPACE_MAX_ARCHIVE_BYTES
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    @property
    def is_open(self) -> bool:
        return self._client is not None and not self._client.is_closed

    async def startup(self) -> None:
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
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            raise UpstreamServiceUnavailableError(
                "GitHub integration service is unavailable.",
                error_code="GITHUB_SERVICE_UNAVAILABLE",
            )
        return self._client

    async def get_project_repository(
        self,
        project_id: uuid.UUID,
        *,
        bearer_token: str,
        request_id: str,
    ) -> ConnectedRepositoryInfo:
        client = self._require_client()
        canonical_id = str(uuid.UUID(str(project_id)))
        path = _GITHUB_STATUS_TEMPLATE.format(project_id=canonical_id)
        try:
            response = await client.get(
                path,
                headers={
                    "Authorization": f"Bearer {bearer_token}",
                    REQUEST_ID_HEADER: request_id,
                    "Accept": "application/json",
                },
            )
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            raise UpstreamServiceUnavailableError(
                "GitHub integration service is temporarily unavailable.",
                error_code="GITHUB_SERVICE_UNAVAILABLE",
            ) from exc

        if response.status_code == 401:
            raise UnauthorizedError("Authentication failed with GitHub Service.")
        if response.status_code == 403:
            raise ForbiddenError("Forbidden to inspect project repository status.")
        if response.status_code == 404:
            raise NotFoundError("Project not found.", error_code="PROJECT_NOT_FOUND")
        if response.status_code != 200:
            raise UpstreamServiceUnavailableError(
                "GitHub integration service returned an unexpected response.",
                error_code="GITHUB_SERVICE_UNAVAILABLE",
            )

        try:
            parsed = _GitHubStatusPayload.model_validate(response.json())
        except (ValueError, PydanticValidationError) as exc:
            raise UpstreamServiceUnavailableError(
                "GitHub integration service returned malformed repository metadata.",
                error_code="GITHUB_SERVICE_UNAVAILABLE",
            ) from exc

        if not parsed.connected or parsed.repository is None:
            raise NotFoundError(
                "No active GitHub repository is connected to this project.",
                error_code="REPOSITORY_NOT_CONNECTED",
            )

        return ConnectedRepositoryInfo(
            project_id=parsed.project_id,
            full_name=parsed.repository.full_name,
            default_branch=parsed.repository.default_branch,
        )

    async def fetch_repository_snapshot(
        self,
        project_id: uuid.UUID,
        *,
        bearer_token: str,
        request_id: str,
        ref: str | None = None,
    ) -> RepositorySnapshotBundle:
        """Download repository zip snapshot solely from GitHub Service (never api.github.com)."""
        repo_info = await self.get_project_repository(
            project_id,
            bearer_token=bearer_token,
            request_id=request_id,
        )
        effective_ref = ref or repo_info.default_branch
        client = self._require_client()
        canonical_id = str(uuid.UUID(str(project_id)))
        path = _GITHUB_SNAPSHOT_TEMPLATE.format(project_id=canonical_id)

        try:
            response = await client.get(
                path,
                params={"ref": effective_ref},
                headers={
                    "Authorization": f"Bearer {bearer_token}",
                    REQUEST_ID_HEADER: request_id,
                    "Accept": "application/zip",
                },
            )
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            raise UpstreamServiceUnavailableError(
                "GitHub integration service is temporarily unavailable while downloading snapshot.",
                error_code="GITHUB_SERVICE_UNAVAILABLE",
            ) from exc

        if response.status_code == 400:
            raise BadRequestError("Invalid Git reference requested.", error_code="INVALID_REF")
        if response.status_code == 401:
            raise UnauthorizedError("Authentication failed with GitHub Service.")
        if response.status_code == 403:
            raise ForbiddenError("Forbidden to download repository snapshot.")
        if response.status_code == 404:
            raise NotFoundError(
                "Connected repository or Git reference was not found.",
                error_code="REPOSITORY_NOT_CONNECTED",
            )
        if response.status_code != 200:
            raise UpstreamServiceUnavailableError(
                "GitHub integration service failed to provide repository snapshot.",
                error_code="GITHUB_SERVICE_UNAVAILABLE",
            )

        archive_bytes = response.content
        if len(archive_bytes) > self._max_archive_bytes:
            raise BadRequestError(
                "Repository snapshot exceeds maximum allowed archive size.",
                error_code="ARCHIVE_TOO_LARGE",
            )

        header_full_name = response.headers.get("x-repository-full-name") or repo_info.full_name
        header_ref = response.headers.get("x-repository-ref") or effective_ref
        return RepositorySnapshotBundle(
            full_name=header_full_name,
            ref=header_ref,
            archive_bytes=archive_bytes,
        )

