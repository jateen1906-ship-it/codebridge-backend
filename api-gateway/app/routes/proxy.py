"""Explicit microservice proxy routing under /api/v1."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from app.clients.http_client import GatewayHTTPClient
from app.clients.service_registry import RouteRule, ServiceRegistry
from app.core.exceptions import build_downstream_error_response
from app.core.logging import get_logger
from app.middleware.correlation import REQUEST_ID_HEADER, get_request_id

logger = get_logger("routes.proxy")

_FORWARDED_HTTP_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"]

# Explicit safe allowlist prevents forwarding Host, hop-by-hop, or spoofed internal headers.
_SAFE_REQUEST_HEADERS = frozenset(
    {
        "accept",
        "accept-language",
        "authorization",
        "cache-control",
        "content-type",
        "idempotency-key",
        "if-match",
        "if-modified-since",
        "if-none-match",
        "if-unmodified-since",
        "user-agent",
    }
)

# Exclude hop-by-hop headers and transport framing headers managed by Starlette/HTTPX.
_EXCLUDED_RESPONSE_HEADERS = frozenset(
    {
        "connection",
        "content-encoding",
        "content-length",
        "content-type",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "server",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "x-powered-by",
        "x-request-id",
    }
)


def _extract_safe_request_headers(request: Request, request_id: str) -> dict[str, str]:
    """Build a sanitized dictionary of outbound headers with correlation ID."""
    outbound: dict[str, str] = {}
    for name, value in request.headers.items():
        if name.lower() in _SAFE_REQUEST_HEADERS:
            outbound[name] = value
    outbound[REQUEST_ID_HEADER] = request_id
    return outbound


def _sanitize_location_header(value: str, base_url: str) -> str:
    """Strip internal microservice host origin if leaked in a Location redirect header."""
    normalized_base = base_url.rstrip("/")
    if value.startswith(normalized_base):
        relative = value[len(normalized_base) :]
        return relative if relative.startswith("/") else f"/{relative}"
    return value


def build_proxy_router(registry: ServiceRegistry) -> APIRouter:
    """Build explicit API Gateway proxy routes ordered by specificity."""
    router = APIRouter(tags=["Gateway Proxy"])

    for rule in registry.list_routes():
        _register_rule_routes(router=router, rule=rule)

    return router


def _register_rule_routes(*, router: APIRouter, rule: RouteRule) -> None:
    """Register exact-prefix and subpath forwarding handlers for a single RouteRule."""

    async def forward_request(request: Request) -> Response:
        service_registry: ServiceRegistry = request.app.state.service_registry
        http_client: GatewayHTTPClient = request.app.state.http_client
        request_id = get_request_id(request)

        path_params = {
            key: str(val)
            for key, val in request.path_params.items()
            if key != "subpath"
        }
        subpath = str(request.path_params.get("subpath", ""))

        target_url, timeout_seconds = service_registry.build_target_url(
            rule=rule,
            path_params=path_params,
            subpath=subpath,
        )
        service_def = service_registry.get_service(rule.service_name)

        outbound_headers = _extract_safe_request_headers(request, request_id)
        query_params = list(request.query_params.multi_items())
        raw_body = await request.body()
        body_payload = raw_body if raw_body else None

        logger.info(
            "Forwarding request to downstream microservice",
            extra={
                "event": "proxy_forward",
                "method": request.method,
                "path": request.url.path,
                "target_service": rule.service_name,
            },
        )

        downstream_response = await http_client.forward(
            service_name=rule.service_name,
            method=request.method,
            url=target_url,
            headers=outbound_headers,
            params=query_params,
            content=body_payload,
            timeout_seconds=timeout_seconds,
        )

        if downstream_response.status_code >= 400:
            return build_downstream_error_response(
                downstream_response=downstream_response,
                request_id=request_id,
                service_name=rule.service_name,
            )

        content_type = downstream_response.headers.get("content-type")
        body_bytes = (
            b""
            if downstream_response.status_code in {204, 304}
            else downstream_response.content
        )

        gateway_response = Response(
            content=body_bytes,
            status_code=downstream_response.status_code,
            media_type=content_type,
        )
        for header_name, header_value in downstream_response.headers.multi_items():
            lowered = header_name.lower()
            if lowered in _EXCLUDED_RESPONSE_HEADERS:
                continue
            if lowered == "location":
                header_value = _sanitize_location_header(header_value, service_def.base_url)
            gateway_response.headers.append(header_name, header_value)

        return gateway_response

    route_name_base = f"proxy_{rule.service_name}_{rule.public_prefix.strip('/').replace('/', '_')}"

    router.add_api_route(
        path=rule.public_prefix,
        endpoint=forward_request,
        methods=_FORWARDED_HTTP_METHODS,
        name=f"{route_name_base}_root",
        include_in_schema=True,
    )
    router.add_api_route(
        path=f"{rule.public_prefix}/{{subpath:path}}",
        endpoint=forward_request,
        methods=_FORWARDED_HTTP_METHODS,
        name=f"{route_name_base}_subpath",
        include_in_schema=True,
    )

