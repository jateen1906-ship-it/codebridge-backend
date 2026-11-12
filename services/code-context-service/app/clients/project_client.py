"""Synchronous HTTP/REST client for Project Service authorization & assignment checks."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError as PydanticValidationError

from app.core.exceptions import (
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    UpstreamServiceUnavailableError,
)
from app.core.logging import get_logger
from app.core.security import REQUEST_ID_HEADER, AuthenticatedUser, UserRole

if TYPE_CHECKING:
    from app.core.config import Settings

logger = get_logger("clients.project")

_PROJECT_BY_ID_TEMPLATE = "/api/v1/projects/{project_id}"


class _ActiveAssignmentPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: uuid.UUID
    freelancer_id: uuid.UUID
    status: str


class _ProjectDetailPayload(BaseModel):
    """Schema matching Project Service GET /api/v1/projects/{project_id} ProjectResponse."""

    model_config = ConfigDict(extra="ignore")

    id: uuid.UUID
    owner_id: uuid.UUID
    status: str
    active_assignment: _ActiveAssignmentPayload | None = None


@dataclass(frozen=True, slots=True)
class ProjectSummary:
    """Verified project & assignment metadata fetched from Project Service."""

    id: uuid.UUID
    owner_id: uuid.UUID
    status: str
    assigned_freelancer_id: uuid.UUID | None = None
    active_assignment_status: str | None = None

    def is_owner(self, user_id: uuid.UUID) -> bool:
        return self.owner_id == user_id

    def is_actively_assigned_freelancer(self, user_id: uuid.UUID) -> bool:
        return (
            self.assigned_freelancer_id is not None
            and self.assigned_freelancer_id == user_id
            and (self.active_assignment_status or "").upper() == "ACTIVE"
        )


def assert_project_access(
    project: ProjectSummary,
    user: AuthenticatedUser,
    *,
    allow_assigned_freelancer: bool = True,
    allow_admin: bool = False,
) -> None:
    """Enforce strict project authorization for indexing, querying, & insights.

    Project Service returns HTTP 200 for OPEN projects to any freelancer (with
    active_assignment=None). Code Context Service MUST additionally verify that a
    freelancer is actively assigned to the project before allowing access to private
    indexed repository context.
    """
    if user.role == UserRole.OWNER:
        if not project.is_owner(user.id):
            raise ForbiddenError("Only the project owner may access this project context.")
        return

    if user.role == UserRole.FREELANCER:
        if not allow_assigned_freelancer:
            raise ForbiddenError("Only the project owner may trigger repository indexing or insights.")
        if not project.is_actively_assigned_freelancer(user.id):
            raise ForbiddenError(
                "Only actively assigned freelancers may access this project's code context."
            )
        return

    if user.role == UserRole.ADMIN:
        if not allow_admin:
            raise ForbiddenError("Administrators are not permitted to perform this operation.")
        return

    raise ForbiddenError("Access denied for this role.")


class ProjectClient:
    """Reusable lifespan-managed HTTPX client communicating strictly with Project Service."""

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = settings.PROJECT_SERVICE_URL.rstrip("/")
        self._timeout_seconds = settings.SERVICE_CLIENT_TIMEOUT_SECONDS
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    @property
    def is_open(self) -> bool:
        return self._client is not None and not self._client.is_closed

    async def startup(self) -> None:
        """Initialize the pooled HTTPX AsyncClient."""
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
        """Close the HTTPX AsyncClient cleanly on shutdown."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            raise UpstreamServiceUnavailableError(
                "Project verification service is unavailable.",
                error_code="PROJECT_SERVICE_UNAVAILABLE",
            )
        return self._client

    async def get_project(
        self,
        project_id: uuid.UUID,
        *,
        bearer_token: str,
        request_id: str,
    ) -> ProjectSummary:
        """Fetch and verify project access and assignment state via Project Service."""
        client = self._require_client()
        canonical_id = str(uuid.UUID(str(project_id)))
        target_path = _PROJECT_BY_ID_TEMPLATE.format(project_id=canonical_id)

        try:
            response = await client.get(
                target_path,
                headers={
                    "Authorization": f"Bearer {bearer_token}",
                    REQUEST_ID_HEADER: request_id,
                    "Accept": "application/json",
                },
            )
        except httpx.TimeoutException as exc:
            logger.warning(
                "Project Service verification timed out",
                extra={
                    "event": "project_client_timeout",
                    "target_service": "project-service",
                    "project_id": canonical_id,
                    "error_code": "PROJECT_SERVICE_UNAVAILABLE",
                },
            )
            raise UpstreamServiceUnavailableError(
                "Project verification service is temporarily unavailable.",
                error_code="PROJECT_SERVICE_UNAVAILABLE",
            ) from exc
        except httpx.RequestError as exc:
            logger.warning(
                "Failed to connect to Project Service",
                extra={
                    "event": "project_client_connect_error",
                    "target_service": "project-service",
                    "project_id": canonical_id,
                    "error_code": "PROJECT_SERVICE_UNAVAILABLE",
                },
            )
            raise UpstreamServiceUnavailableError(
                "Project verification service is temporarily unavailable.",
                error_code="PROJECT_SERVICE_UNAVAILABLE",
            ) from exc

        if response.status_code == 401:
            raise UnauthorizedError("Invalid or expired authentication token.")
        if response.status_code == 403:
            raise ForbiddenError("You do not have permission to access this project.")
        if response.status_code == 404:
            raise NotFoundError("Project not found.", error_code="PROJECT_NOT_FOUND")
        if response.status_code != 200:
            logger.warning(
                "Project Service returned unexpected status during verification",
                extra={
                    "event": "project_client_unexpected_status",
                    "target_service": "project-service",
                    "project_id": canonical_id,
                    "status_code": response.status_code,
                    "error_code": "PROJECT_SERVICE_UNAVAILABLE",
                },
            )
            raise UpstreamServiceUnavailableError(
                "Project verification service is temporarily unavailable.",
                error_code="PROJECT_SERVICE_UNAVAILABLE",
            )

        try:
            parsed = _ProjectDetailPayload.model_validate(response.json())
        except (ValueError, PydanticValidationError) as exc:
            logger.warning(
                "Malformed project response from Project Service",
                extra={
                    "event": "project_client_malformed_response",
                    "target_service": "project-service",
                    "project_id": canonical_id,
                    "error_code": "PROJECT_SERVICE_UNAVAILABLE",
                },
            )
            raise UpstreamServiceUnavailableError(
                "Project verification returned an invalid response.",
                error_code="PROJECT_SERVICE_UNAVAILABLE",
            ) from exc

        if parsed.id != uuid.UUID(canonical_id):
            raise UpstreamServiceUnavailableError(
                "Project verification returned mismatched project metadata.",
                error_code="PROJECT_SERVICE_UNAVAILABLE",
            )

        assigned_freelancer_id: uuid.UUID | None = None
        active_assignment_status: str | None = None
        if parsed.active_assignment is not None:
            assigned_freelancer_id = parsed.active_assignment.freelancer_id
            active_assignment_status = parsed.active_assignment.status

        return ProjectSummary(
            id=parsed.id,
            owner_id=parsed.owner_id,
            status=parsed.status,
            assigned_freelancer_id=assigned_freelancer_id,
            active_assignment_status=active_assignment_status,
        )

