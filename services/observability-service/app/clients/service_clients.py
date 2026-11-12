"""HTTPX clients for Identity verification and concurrent platform service health probing."""

from __future__ import annotations

import asyncio
import time
import uuid

import httpx

from app.core.config import Settings
from app.core.dependencies import AuthenticatedUser
from app.core.exceptions import UnauthorizedError, UpstreamServiceUnavailableError
from app.schemas.observability import ServiceHealthSnapshot


class IdentityClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(timeout=timeout_seconds, connect=min(5.0, timeout_seconds))
        self._transport = transport

    async def get_current_user(
        self,
        *,
        access_token: str,
        request_id: str | None = None,
    ) -> AuthenticatedUser:
        headers: dict[str, str] = {"Authorization": f"Bearer {access_token}"}
        if request_id:
            headers["X-Request-ID"] = request_id
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                resp = await client.get("/api/v1/auth/me", headers=headers)
        except httpx.HTTPError as exc:
            raise UpstreamServiceUnavailableError("Unable to reach Identity Service.") from exc

        if resp.status_code == 401:
            raise UnauthorizedError("Invalid or expired authentication token.")
        if resp.status_code == 403:
            raise UnauthorizedError("User account is suspended or forbidden.")
        if resp.status_code != 200:
            raise UpstreamServiceUnavailableError("Identity Service authentication failed.")

        data = resp.json()
        return AuthenticatedUser(
            id=uuid.UUID(str(data["id"])),
            full_name=str(data.get("full_name", "")),
            email=str(data.get("email", "")),
            role=data["role"],
            is_active=bool(data.get("is_active", True)),
            is_verified=bool(data.get("is_verified", True)),
            access_token=access_token,
        )


class ServiceProbeClient:
    """Concurrently probes /health and /ready across API Gateway and all 12 microservices."""

    def __init__(
        self,
        *,
        settings: Settings,
        transport_map: dict[str, httpx.AsyncBaseTransport] | None = None,
    ) -> None:
        self._timeout = httpx.Timeout(
            timeout=settings.OBSERVABILITY_PROBE_TIMEOUT_SECONDS,
            connect=min(2.0, settings.OBSERVABILITY_PROBE_TIMEOUT_SECONDS),
        )
        self._transport_map = transport_map or {}
        self._targets: list[tuple[str, int, str]] = [
            ("api-gateway", 8000, settings.API_GATEWAY_URL),
            ("identity-service", 8001, settings.IDENTITY_SERVICE_URL),
            ("project-service", 8002, settings.PROJECT_SERVICE_URL),
            ("github-service", 8003, settings.GITHUB_SERVICE_URL),
            ("code-context-service", 8004, settings.CODE_CONTEXT_SERVICE_URL),
            ("workspace-service", 8005, settings.WORKSPACE_SERVICE_URL),
            ("review-service", 8006, settings.REVIEW_SERVICE_URL),
            ("verification-service", 8007, settings.VERIFICATION_SERVICE_URL),
            ("payment-service", 8008, settings.PAYMENT_SERVICE_URL),
            ("notification-service", 8009, settings.NOTIFICATION_SERVICE_URL),
            ("admin-service", 8010, settings.ADMIN_SERVICE_URL),
            ("audit-service", 8011, settings.AUDIT_SERVICE_URL),
        ]

    async def probe_target(
        self,
        service_name: str,
        port: int,
        base_url: str,
        request_id: str | None = None,
    ) -> ServiceHealthSnapshot:
        headers = {"X-Request-ID": request_id} if request_id else {}
        transport = self._transport_map.get(service_name)
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                base_url=base_url.rstrip("/"),
                timeout=self._timeout,
                transport=transport,
            ) as client:
                health_resp, ready_resp = await asyncio.gather(
                    client.get("/health", headers=headers),
                    client.get("/ready", headers=headers),
                )
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            health_ok = health_resp.status_code == 200
            if ready_resp.status_code == 404:
                ready_state = "unsupported"
            elif ready_resp.status_code == 200:
                ready_state = "ready"
            else:
                ready_state = "not_ready"

            return ServiceHealthSnapshot(
                service_name=service_name,
                port=port,
                health_status="healthy" if health_ok else "unhealthy",
                ready_status=ready_state,
                health_http_status=health_resp.status_code,
                ready_http_status=ready_resp.status_code,
                latency_ms=latency_ms,
                detail=None if (health_ok and ready_state in {"ready", "unsupported"}) else "Probe returned non-200 status.",
            )
        except httpx.HTTPError as exc:
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            return ServiceHealthSnapshot(
                service_name=service_name,
                port=port,
                health_status="unreachable",
                ready_status="unreachable",
                health_http_status=None,
                ready_http_status=None,
                latency_ms=latency_ms,
                detail=exc.__class__.__name__,
            )

    async def probe_all_peers(self, request_id: str | None = None) -> list[ServiceHealthSnapshot]:
        tasks = [
            self.probe_target(name, port, url, request_id=request_id)
            for name, port, url in self._targets
        ]
        return list(await asyncio.gather(*tasks))
