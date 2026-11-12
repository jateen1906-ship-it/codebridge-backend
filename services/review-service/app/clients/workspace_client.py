"""Optional HTTPX client for verifying workspace association and emitting notifications/audit."""

from __future__ import annotations

import uuid

import httpx

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.core.logging import get_logger

logger = get_logger("clients")


class WorkspaceClient:
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

    async def verify_workspace_belongs_to_project(
        self,
        *,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        access_token: str,
        request_id: str | None = None,
    ) -> None:
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
                    f"/api/v1/workspace/{workspace_id}",
                    headers=headers,
                )
        except httpx.RequestError:
            # Workspace Service outage must not prevent submission if freelancer provides workspace ID
            logger.warning("Workspace verification skipped due to unreachable Workspace Service")
            return

        if response.status_code == 404:
            raise NotFoundError("Referenced workspace does not exist.", error_code="WORKSPACE_NOT_FOUND")
        if response.status_code == 403:
            raise ForbiddenError("You do not have permission to attach that workspace.")
        if response.status_code == 200:
            data = response.json()
            if str(data.get("project_id")) != str(project_id):
                raise ConflictError(
                    "Referenced workspace does not belong to this project.",
                    error_code="WORKSPACE_PROJECT_MISMATCH",
                )


class TelemetryRelayClient:
    """Non-blocking synchronous HTTPX emitter for notification-service and audit-service."""

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
            logger.debug("Notification emission skipped or failed softly", exc_info=False)

    async def audit(
        self,
        *,
        actor_id: uuid.UUID,
        actor_role: str,
        action: str,
        resource_type: str,
        resource_id: str,
        project_id: uuid.UUID,
        outcome: str = "SUCCESS",
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
                        "service_name": "review-service",
                        "actor_id": str(actor_id),
                        "actor_role": actor_role,
                        "action": action,
                        "resource_type": resource_type,
                        "resource_id": resource_id,
                        "project_id": str(project_id),
                        "outcome": outcome,
                        "metadata": metadata or {},
                    },
                )
        except Exception:
            logger.debug("Audit emission skipped or failed softly", exc_info=False)

