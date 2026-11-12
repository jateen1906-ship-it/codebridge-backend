"""Synchronous HTTPX clients for Admin Service."""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from app.core.dependencies import AuthenticatedUser
from app.core.exceptions import (
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    UpstreamServiceUnavailableError,
)
from app.core.logging import get_logger

logger = get_logger("clients")


class IdentityAdminClient:
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
        h = {"Authorization": f"Bearer {access_token}"}
        if request_id:
            h["X-Request-ID"] = request_id
        return h

    async def get_current_user(
        self,
        *,
        access_token: str,
        request_id: str | None = None,
    ) -> AuthenticatedUser:
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                resp = await client.get("/api/v1/auth/me", headers=self._headers(access_token, request_id))
        except httpx.RequestError as exc:
            raise UpstreamServiceUnavailableError("Identity Service unavailable.") from exc

        if resp.status_code == 401:
            raise UnauthorizedError("Invalid or expired access token.")
        if resp.status_code != 200:
            raise UpstreamServiceUnavailableError()

        data = resp.json()
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

    async def list_users(
        self,
        *,
        access_token: str,
        page: int,
        page_size: int,
        role: str | None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if role:
            params["role"] = role
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                resp = await client.get(
                    "/api/v1/auth/admin/users",
                    params=params,
                    headers=self._headers(access_token, request_id),
                )
        except httpx.RequestError as exc:
            raise UpstreamServiceUnavailableError("Identity Service unavailable.") from exc

        if resp.status_code == 403:
            raise ForbiddenError()
        if resp.status_code != 200:
            raise UpstreamServiceUnavailableError("Failed to list identity accounts.")
        return resp.json()

    async def set_user_active_status(
        self,
        *,
        user_id: uuid.UUID,
        is_active: bool,
        access_token: str,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                resp = await client.patch(
                    f"/api/v1/auth/admin/users/{user_id}/status",
                    json={"is_active": is_active},
                    headers=self._headers(access_token, request_id),
                )
        except httpx.RequestError as exc:
            raise UpstreamServiceUnavailableError("Identity Service unavailable.") from exc

        if resp.status_code == 404:
            raise NotFoundError("Target user account was not found.", error_code="USER_NOT_FOUND")
        if resp.status_code == 403:
            raise ForbiddenError()
        if resp.status_code != 200:
            raise UpstreamServiceUnavailableError("Identity Service rejected user status update.")
        return resp.json()


class PlatformInspectionClient:
    """Fetches sanitized non-secret project & audit telemetry for Admin inspection."""

    def __init__(
        self,
        *,
        project_url: str,
        audit_url: str,
        notification_url: str,
        internal_service_key: str,
        timeout_seconds: float,
        project_transport: httpx.AsyncBaseTransport | None = None,
        audit_transport: httpx.AsyncBaseTransport | None = None,
        notification_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._project_url = project_url.rstrip("/")
        self._audit_url = audit_url.rstrip("/")
        self._notification_url = notification_url.rstrip("/")
        self._service_key = internal_service_key
        self._timeout = httpx.Timeout(timeout_seconds)
        self._project_transport = project_transport
        self._audit_transport = audit_transport
        self._notification_transport = notification_transport

    async def list_projects(
        self,
        *,
        access_token: str,
        page: int,
        page_size: int,
        status_filter: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {access_token}"}
        if request_id:
            headers["X-Request-ID"] = request_id
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if status_filter:
            params["status"] = status_filter
        try:
            async with httpx.AsyncClient(
                base_url=self._project_url,
                timeout=self._timeout,
                transport=self._project_transport,
            ) as client:
                resp = await client.get("/api/v1/projects", params=params, headers=headers)
        except httpx.RequestError as exc:
            raise UpstreamServiceUnavailableError("Project Service unavailable.") from exc
        if resp.status_code != 200:
            raise UpstreamServiceUnavailableError("Failed to inspect projects.")
        return resp.json()

    async def list_audit_logs(
        self,
        *,
        access_token: str,
        service_name: str | None,
        project_id: uuid.UUID | None,
        limit: int,
        request_id: str | None,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {access_token}"}
        if request_id:
            headers["X-Request-ID"] = request_id
        params: dict[str, Any] = {"limit": limit}
        if service_name:
            params["service_name"] = service_name
        if project_id:
            params["project_id"] = str(project_id)
        try:
            async with httpx.AsyncClient(
                base_url=self._audit_url,
                timeout=self._timeout,
                transport=self._audit_transport,
            ) as client:
                resp = await client.get("/api/v1/audit/events", params=params, headers=headers)
        except httpx.RequestError as exc:
            raise UpstreamServiceUnavailableError("Audit Service unavailable.") from exc
        if resp.status_code != 200:
            raise UpstreamServiceUnavailableError("Failed to inspect audit logs.")
        return resp.json()

    async def emit_audit(
        self,
        *,
        actor_id: uuid.UUID,
        action: str,
        resource_type: str,
        resource_id: str,
        metadata: dict[str, Any],
        request_id: str | None,
    ) -> None:
        headers = {"X-Service-Key": self._service_key}
        if request_id:
            headers["X-Request-ID"] = request_id
        try:
            async with httpx.AsyncClient(
                base_url=self._audit_url,
                timeout=httpx.Timeout(3.0),
                transport=self._audit_transport,
            ) as client:
                await client.post(
                    "/api/v1/audit/events",
                    headers=headers,
                    json={
                        "service_name": "admin-service",
                        "actor_id": str(actor_id),
                        "actor_role": "ADMIN",
                        "action": action,
                        "resource_type": resource_type,
                        "resource_id": resource_id,
                        "outcome": "SUCCESS",
                        "metadata": metadata,
                    },
                )
        except Exception:
            logger.debug("Admin audit forwarding skipped softly")
