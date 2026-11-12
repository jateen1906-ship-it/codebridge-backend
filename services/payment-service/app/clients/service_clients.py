"""Synchronous HTTPX service clients for Payment Service."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

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
class ProjectBillingInfo:
    id: uuid.UUID
    owner_id: uuid.UUID
    title: str
    status: str
    budget: Decimal
    currency: str
    assigned_freelancer_id: uuid.UUID | None


@dataclass(frozen=True)
class ReviewApprovalInfo:
    approved: bool
    approved_submission_id: uuid.UUID | None
    approved_freelancer_id: uuid.UUID | None


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
    ) -> ProjectBillingInfo:
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
        return ProjectBillingInfo(
            id=uuid.UUID(str(data["id"])),
            owner_id=uuid.UUID(str(data["owner_id"])),
            title=str(data.get("title", "")),
            status=str(data["status"]),
            budget=Decimal(str(data["budget"])).quantize(Decimal("0.01")),
            currency=str(data.get("currency", "USD")).upper(),
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

    async def require_project_approval(
        self,
        *,
        project_id: uuid.UUID,
        access_token: str,
        request_id: str | None = None,
    ) -> ReviewApprovalInfo:
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
                    f"/api/v1/review/projects/{project_id}",
                    headers=headers,
                )
        except httpx.RequestError as exc:
            raise UpstreamServiceUnavailableError("Review Service is temporarily unavailable.") from exc

        if response.status_code == 404:
            raise ConflictError(
                "No approved submission exists for this project yet.",
                error_code="REVIEW_APPROVAL_REQUIRED",
            )
        if response.status_code == 403:
            raise ForbiddenError("Not authorized to inspect project review approval.")
        if response.status_code != 200:
            raise UpstreamServiceUnavailableError("Review Service failed approval verification.")

        data = response.json()
        approved = bool(data.get("approved", False))
        if not approved:
            raise ConflictError(
                "Escrow release is blocked until Review Service confirms an APPROVED submission.",
                error_code="REVIEW_APPROVAL_REQUIRED",
            )
        sub_id = data.get("approved_submission_id")
        freelancer_id = data.get("approved_freelancer_id")
        return ReviewApprovalInfo(
            approved=True,
            approved_submission_id=uuid.UUID(str(sub_id)) if sub_id else None,
            approved_freelancer_id=uuid.UUID(str(freelancer_id)) if freelancer_id else None,
        )


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
                        "service_name": "payment-service",
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
