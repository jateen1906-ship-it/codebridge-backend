"""HTTPX client for reading project metadata and syncing review workflow state."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import httpx

from app.core.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UpstreamServiceUnavailableError,
)


@dataclass(frozen=True)
class ProjectSummary:
    id: uuid.UUID
    owner_id: uuid.UUID
    title: str
    status: str
    assigned_freelancer_id: uuid.UUID | None
    assignment_status: str | None


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

    def _headers(self, access_token: str, request_id: str | None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {access_token}"}
        if request_id:
            headers["X-Request-ID"] = request_id
        return headers

    def _parse(self, data: dict) -> ProjectSummary:
        aa = data.get("active_assignment")
        assigned_id = uuid.UUID(str(aa["freelancer_id"])) if isinstance(aa, dict) and aa.get("freelancer_id") else None
        assignment_status = str(aa["status"]) if isinstance(aa, dict) and aa.get("status") else None
        return ProjectSummary(
            id=uuid.UUID(str(data["id"])),
            owner_id=uuid.UUID(str(data["owner_id"])),
            title=str(data.get("title", "")),
            status=str(data["status"]),
            assigned_freelancer_id=assigned_id,
            assignment_status=assignment_status,
        )

    async def get_project(
        self,
        *,
        project_id: uuid.UUID,
        access_token: str,
        request_id: str | None = None,
    ) -> ProjectSummary:
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.get(
                    f"/api/v1/projects/{project_id}",
                    headers=self._headers(access_token, request_id),
                )
        except httpx.RequestError as exc:
            raise UpstreamServiceUnavailableError("Project Service is temporarily unavailable.") from exc

        if response.status_code == 404:
            raise NotFoundError("Project not found.", error_code="PROJECT_NOT_FOUND")
        if response.status_code == 403:
            raise ForbiddenError("You do not have permission to access this project.")
        if response.status_code != 200:
            raise UpstreamServiceUnavailableError("Project Service returned an unexpected response.")

        return self._parse(response.json())

    async def transition_workflow(
        self,
        *,
        project_id: uuid.UUID,
        target_status: str,
        approval_verified: bool,
        access_token: str,
        request_id: str | None = None,
    ) -> ProjectSummary:
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    f"/api/v1/projects/{project_id}/workflow/transition",
                    headers=self._headers(access_token, request_id),
                    json={
                        "target_status": target_status,
                        "approval_verified": approval_verified,
                    },
                )
        except httpx.RequestError as exc:
            raise UpstreamServiceUnavailableError("Project Service is temporarily unavailable.") from exc

        if response.status_code == 404:
            raise NotFoundError("Project not found.", error_code="PROJECT_NOT_FOUND")
        if response.status_code == 403:
            raise ForbiddenError("Not authorized to transition project status.")
        if response.status_code == 409:
            raise ConflictError("Project status transition conflict.", error_code="PROJECT_STATUS_CONFLICT")
        if response.status_code != 200:
            raise UpstreamServiceUnavailableError("Project Service rejected workflow transition.")

        return self._parse(response.json())

