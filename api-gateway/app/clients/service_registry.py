"""Centralized service registry and deterministic route-matching rules."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.core.exceptions import GatewayValidationError, ServiceConfigurationError


@dataclass(frozen=True, slots=True)
class RouteRule:
    """Explicit public-to-internal routing rule owned by a registered microservice."""

    service_name: str
    public_prefix: str
    internal_target_path: str
    priority: int = 100


@dataclass(frozen=True, slots=True)
class ServiceDefinition:
    """Metadata and transport settings for a registered downstream microservice."""

    service_name: str
    base_url: str
    public_prefixes: tuple[str, ...]
    internal_target_paths: tuple[str, ...]
    timeout_seconds: float


def _validate_path_segment(value: str) -> str:
    """Reject path traversal or control characters in single path parameters."""
    if (
        not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
        or any(ord(ch) < 32 for ch in value)
    ):
        raise GatewayValidationError("Invalid path parameter.")
    return value


def _validate_subpath(subpath: str) -> str:
    """Validate and normalize relative downstream subpath segments."""
    if not subpath:
        return ""
    if "\\" in subpath or "\x00" in subpath or any(ord(ch) < 32 for ch in subpath):
        raise GatewayValidationError("Invalid request path.")
    lowered = subpath.lower()
    if lowered.startswith(("http://", "https://", "//")):
        raise GatewayValidationError("Invalid request path.")

    cleaned = subpath.strip("/")
    if not cleaned:
        return ""

    segments = cleaned.split("/")
    for seg in segments:
        if not seg or seg in {".", ".."}:
            raise GatewayValidationError("Invalid path traversal sequence.")
    return "/".join(segments)


class ServiceRegistry:
    """Registry mapping explicit public API route groups to internal microservices."""

    def __init__(
        self,
        services: dict[str, ServiceDefinition],
        routes: list[RouteRule],
    ) -> None:
        self._services = dict(services)
        self._routes = sorted(
            routes,
            key=lambda rule: (rule.priority, -len(rule.public_prefix)),
        )

    def get_service(self, service_name: str) -> ServiceDefinition:
        """Retrieve a registered service definition, ensuring it has a valid base URL."""
        service = self._services.get(service_name)
        if service is None or not service.base_url or not service.base_url.strip():
            raise ServiceConfigurationError()
        return service

    def list_services(self) -> list[ServiceDefinition]:
        """Return all registered services."""
        return list(self._services.values())

    def list_routes(self) -> list[RouteRule]:
        """Return all routing rules ordered by matching specificity."""
        return list(self._routes)

    def build_target_url(
        self,
        rule: RouteRule,
        path_params: dict[str, str] | None = None,
        subpath: str = "",
    ) -> tuple[str, float]:
        """Construct the internal downstream target URL and timeout for a matched rule."""
        service = self.get_service(rule.service_name)

        safe_params: dict[str, str] = {}
        for key, raw_val in (path_params or {}).items():
            safe_params[key] = _validate_path_segment(raw_val)

        target_path = rule.internal_target_path.format(**safe_params).rstrip("/")
        clean_subpath = _validate_subpath(subpath)
        if clean_subpath:
            target_path = f"{target_path}/{clean_subpath}"

        base = service.base_url.rstrip("/")
        return f"{base}{target_path}", service.timeout_seconds


def build_service_registry(settings: Settings) -> ServiceRegistry:
    """Create and validate the centralized ServiceRegistry from Gateway settings."""
    api_prefix = settings.API_V1_PREFIX.rstrip("/")
    default_timeout = settings.REQUEST_TIMEOUT_SECONDS

    service_specs: list[tuple[str, str, tuple[tuple[str, int] | tuple[str, int, str], ...]]] = [
        ("identity-service", settings.IDENTITY_SERVICE_URL, (("/auth", 100), ("/freelancers", 100), ("/profile", 100))),
        ("project-service", settings.PROJECT_SERVICE_URL, (("/projects", 100),)),
        (
            "github-service",
            settings.GITHUB_SERVICE_URL,
            (
                ("/projects/{project_id}/github", 10),
                ("/github", 100),
            ),
        ),
        ("code-context-service", settings.CODE_CONTEXT_SERVICE_URL, (("/code-context", 100),)),
        ("workspace-service", settings.WORKSPACE_SERVICE_URL, (("/workspace", 100),)),
        ("review-service", settings.REVIEW_SERVICE_URL, (("/submissions", 100), ("/review", 100))),
        (
            "verification-service",
            settings.VERIFICATION_SERVICE_URL,
            (
                ("/profile/verified-work", 10, "/verification/me"),
                ("/verification", 100),
            ),
        ),
        ("payment-service", settings.PAYMENT_SERVICE_URL, (("/payment", 100),)),
        ("notification-service", settings.NOTIFICATION_SERVICE_URL, (("/notifications", 100),)),
        ("admin-service", settings.ADMIN_SERVICE_URL, (("/admin", 100),)),
        ("audit-service", settings.AUDIT_SERVICE_URL, (("/audit", 100),)),
        ("observability-service", settings.OBSERVABILITY_SERVICE_URL, (("/observability", 100),)),
    ]

    services: dict[str, ServiceDefinition] = {}
    routes: list[RouteRule] = []

    for service_name, base_url, prefix_specs in service_specs:
        if not base_url or not base_url.strip():
            raise ServiceConfigurationError(
                f"Missing base URL configuration for service '{service_name}'."
            )

        public_prefixes: list[str] = []
        internal_paths: list[str] = []
        for spec in prefix_specs:
            if len(spec) == 3:
                public_prefix, priority, internal_suffix = spec
            else:
                public_prefix, priority = spec
                internal_suffix = public_prefix
            internal_target = f"{api_prefix}{internal_suffix}"
            public_prefixes.append(public_prefix)
            internal_paths.append(internal_target)
            routes.append(
                RouteRule(
                    service_name=service_name,
                    public_prefix=public_prefix,
                    internal_target_path=internal_target,
                    priority=priority,
                )
            )

        services[service_name] = ServiceDefinition(
            service_name=service_name,
            base_url=base_url.rstrip("/"),
            public_prefixes=tuple(public_prefixes),
            internal_target_paths=tuple(internal_paths),
            timeout_seconds=default_timeout,
        )

    return ServiceRegistry(services=services, routes=routes)

