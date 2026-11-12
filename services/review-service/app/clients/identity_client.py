"""HTTPX client for authenticating users via Identity Service."""

from __future__ import annotations

import uuid

import httpx

from app.core.exceptions import UnauthorizedError, UpstreamServiceUnavailableError
from app.core.security import AuthenticatedUser


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
            raise UpstreamServiceUnavailableError(
                "Identity Service is unavailable for authentication."
            ) from exc

        if response.status_code == 401:
            raise UnauthorizedError("Invalid, expired, or revoked access token.")
        if response.status_code != 200:
            raise UpstreamServiceUnavailableError("Identity Service failed token validation.")

        try:
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
        except (ValueError, KeyError, TypeError) as exc:
            raise UpstreamServiceUnavailableError("Malformed user profile from Identity Service.") from exc

