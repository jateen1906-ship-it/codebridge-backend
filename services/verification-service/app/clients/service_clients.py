"""Synchronous HTTPX service clients for Verification Service."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from app.core.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    UpstreamServiceUnavailableError,
)
from app.core.logging import get_logger
from app.core.security import AuthenticatedUser

logger = get_logger("clients")


@dataclass(frozen=True)
class ProjectSummary:
    id: uuid.UUID
    owner_id: uuid.UUID
    title: str
    status: str
    assigned_freelancer_id: uuid.UUID | None


@dataclass(frozen=True)
class ApprovedSubmissionInfo:
    id: uuid.UUID
    project_id: uuid.UUID
    freelancer_id: uuid.UUID
    owner_id: uuid.UUID
    title: str
    status: str
    commit_sha: str | None
    reviewed_at: datetime


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
            raise UpstreamServiceUnavailableError("Identity Service is unavailable.") from exc

        if response.status_code == 401:
            raise UnauthorizedError("Invalid or expired access token.")
        if response.status_code != 200:
            raise UpstreamServiceUnavailableError("Identity Service authentication failed.")

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

    async def get_project(
        self,
        *,
        project_id: uuid.UUID,
        access_token: str,
        request_id: str | None = None,
    ) -> ProjectSummary:
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
            raise UpstreamServiceUnavailableError("Project Service is temporarily unavailable.") from exc

        if response.status_code == 404:
            raise NotFoundError("Project not found.", error_code="PROJECT_NOT_FOUND")
        if response.status_code == 403:
            raise ForbiddenError("You do not have permission to access this project.")
        if response.status_code != 200:
            raise UpstreamServiceUnavailableError("Project Service lookup failed.")

        data = response.json()
        aa = data.get("active_assignment")
        assigned = uuid.UUID(str(aa["freelancer_id"])) if isinstance(aa, dict) and aa.get("freelancer_id") else None
        return ProjectSummary(
            id=uuid.UUID(str(data["id"])),
            owner_id=uuid.UUID(str(data["owner_id"])),
            title=str(data.get("title", "")),
            status=str(data["status"]),
            assigned_freelancer_id=assigned,
        )


class ReviewClient:
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

    async def verify_approved_submission(
        self,
        *,
        project_id: uuid.UUID,
        submission_id: uuid.UUID,
        access_token: str,
        request_id: str | None = None,
    ) -> ApprovedSubmissionInfo:
        headers = {"Authorization": f"Bearer {access_token}"}
        if request_id:
            headers["X-Request-ID"] = request_id
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.get(
                    f"/api/v1/review/submissions/{submission_id}",
                    headers=headers,
                )
        except httpx.RequestError as exc:
            raise UpstreamServiceUnavailableError("Review Service is temporarily unavailable.") from exc

        if response.status_code == 404:
            raise NotFoundError("Submission not found in Review Service.", error_code="SUBMISSION_NOT_FOUND")
        if response.status_code == 403:
            raise ForbiddenError("You do not have permission to inspect this submission.")
        if response.status_code != 200:
            raise UpstreamServiceUnavailableError("Review Service lookup failed.")

        data = response.json()
        if str(data.get("project_id")) != str(project_id):
            raise ConflictError(
                "Submission does not belong to the specified project.",
                error_code="SUBMISSION_PROJECT_MISMATCH",
            )
        if str(data.get("status")) != "APPROVED":
            raise ConflictError(
                f"Verified work records require an APPROVED submission (current status: {data.get('status')}).",
                error_code="SUBMISSION_NOT_APPROVED",
            )
        reviewed_raw = data.get("reviewed_at") or data.get("updated_at")
        reviewed_at = (
            datetime.fromisoformat(str(reviewed_raw).replace("Z", "+00:00"))
            if reviewed_raw
            else datetime.now(timezone.utc)
        )
        return ApprovedSubmissionInfo(
            id=uuid.UUID(str(data["id"])),
            project_id=uuid.UUID(str(data["project_id"])),
            freelancer_id=uuid.UUID(str(data["freelancer_id"])),
            owner_id=uuid.UUID(str(data["owner_id"])),
            title=str(data["title"]),
            status=str(data["status"]),
            commit_sha=data.get("commit_sha"),
            reviewed_at=reviewed_at,
        )


class GitHubMetadataClient:
    """Best-effort repository name lookup via GitHub Service (never fails verification if unlinked)."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(min(timeout_seconds, 5.0))
        self._transport = transport

    async def get_repository_full_name(
        self,
        *,
        project_id: uuid.UUID,
        access_token: str,
        request_id: str | None = None,
    ) -> str | None:
        headers = {"Authorization": f"Bearer {access_token}"}
        if request_id:
            headers["X-Request-ID"] = request_id
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.get(
                    f"/api/v1/projects/{project_id}/github/status",
                    headers=headers,
                )
            if response.status_code == 200:
                data = response.json()
                repo = data.get("repository")
                if isinstance(repo, dict) and repo.get("full_name"):
                    return str(repo["full_name"])
        except Exception:
            logger.debug("GitHub metadata lookup skipped softly")
        return None


class TelemetryRelayClient:
    def __init__(
        self,
        *,
        notification_base_url: str,
        audit_base_url: str,
        internal_service_key: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._notification_url = notification_base_url.rstrip("/")
        self._audit_url = audit_base_url.rstrip("/")
        self._service_key = internal_service_key
        self._timeout = httpx.Timeout(min(timeout_seconds, 3.0))
        self._transport = transport

    async def notify(
        self,
        *,
        user_id: uuid.UUID,
        event_type: str,
        title: str,
        message: str,
        project_id: uuid.UUID,
        idempotency_key: str,
        request_id: str | None = None,
    ) -> None:
        headers = {"X-Service-Key": self._service_key}
        if request_id:
            headers["X-Request-ID"] = request_id
        try:
            async with httpx.AsyncClient(
                base_url=self._notification_url,
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                await client.post(
                    "/api/v1/notifications/events",
                    headers=headers,
                    json={
                        "user_id": str(user_id),
                        "event_type": event_type,
                        "title": title,
                        "message": message,
                        "resource_type": "project",
                        "resource_id": str(project_id),
                        "idempotency_key": idempotency_key,
                        "send_email": True,
                    },
                )
        except Exception:
            logger.debug("Notification emission skipped softly")

    async def audit(
        self,
        *,
        actor_id: uuid.UUID,
        actor_role: str,
        action: str,
        resource_type: str,
        resource_id: str,
        project_id: uuid.UUID,
        metadata: dict | None = None,
        request_id: str | None = None,
    ) -> None:
        headers = {"X-Service-Key": self._service_key}
        if request_id:
            headers["X-Request-ID"] = request_id
        try:
            async with httpx.AsyncClient(
                base_url=self._audit_url,
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                await client.post(
                    "/api/v1/audit/events",
                    headers=headers,
                    json={
                        "service_name": "verification-service",
                        "actor_id": str(actor_id),
                        "actor_role": actor_role,
                        "action": action,
                        "resource_type": resource_type,
                        "resource_id": resource_id,
                        "project_id": str(project_id),
                        "outcome": "SUCCESS",
                        "metadata": metadata or {},
                    },
                )
        except Exception:
            logger.debug("Audit emission skipped softly")
