"""HTTPX clients for Identity and Project access verification in Audit Service."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import httpx

from app.core.dependencies import AuthenticatedUser
from app.core.exceptions import (
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    UpstreamServiceUnavailableError,
)


@dataclass(frozen=True)
class ProjectAccessInfo:
    id: uuid.UUID
    owner_id: uuid.UUID
    assigned_freelancer_id: uuid.UUID | None


class IdentityClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(timeout_seconds)
        self._transport = transport

    async def get_current_user(
        self,
        *,
        access_token: str,
        request_id: str | None = None,
    ) -> AuthenticatedUser:
        headers = {"Authorization": f"Bearer {access_token}"}
        if request_id:
            headers["X-Request-ID"] = request_id
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.get("/api/v1/auth/me", headers=headers)
        except httpx.RequestError as exc:
            raise UpstreamServiceUnavailableError("Identity Service unavailable.") from exc

        if response.status_code == 401:
            raise UnauthorizedError("Invalid or expired access token.")
        if response.status_code != 200:
            raise UpstreamServiceUnavailableError()

        data = response.json()
        user = data.get("user") if isinstance(data, dict) and "user" in data else data
        return AuthenticatedUser(
            id=uuid.UUID(str(user["id"])),
            full_name=str(user["full_name"]),
            email=str(user["email"]),
            role=str(user["role"]),  # type: ignore[arg-type]
            is_active=bool(user.get("is_active", True)),
            is_verified=bool(user.get("is_verified", True)),
            access_token=access_token,
        )


class ProjectClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(timeout_seconds)
        self._transport = transport

    async def get_project_access(
        self,
        *,
        project_id: uuid.UUID,
        access_token: str,
        request_id: str | None = None,
    ) -> ProjectAccessInfo:
        headers = {"Authorization": f"Bearer {access_token}"}
        if request_id:
            headers["X-Request-ID"] = request_id
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.get(f"/api/v1/projects/{project_id}", headers=headers)
        except httpx.RequestError as exc:
            raise UpstreamServiceUnavailableError("Project Service unavailable.") from exc

        if response.status_code == 404:
            raise NotFoundError("Project not found.", error_code="PROJECT_NOT_FOUND")
        if response.status_code == 403:
            raise ForbiddenError("You do not have permission to inspect audit logs for this project.")
        if response.status_code != 200:
            raise UpstreamServiceUnavailableError()

        data = response.json()
        aa = data.get("active_assignment")
        assigned = uuid.UUID(str(aa["freelancer_id"])) if isinstance(aa, dict) and aa.get("freelancer_id") else None
        return ProjectAccessInfo(
            id=uuid.UUID(str(data["id"])),
            owner_id=uuid.UUID(str(data["owner_id"])),
            assigned_freelancer_id=assigned,
        )
