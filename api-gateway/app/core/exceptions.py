"""Standardized Gateway exceptions and JSON error response handlers."""

from __future__ import annotations

import re
import uuid
from typing import Any

import httpx
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger, request_id_ctx

logger = get_logger("exceptions")

_SAFE_CODE_RE = re.compile(r"^[A-Z0-9_]{2,64}$")
_INTERNAL_URL_RE = re.compile(r"https?://[^\s/$.?#].[^\s]*", re.IGNORECASE)


class GatewayError(Exception):
    """Base exception for errors originating within the API Gateway."""

    def __init__(
        self,
        *,
        status_code: int,
        error_code: str,
        message: str,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.message = message


class GatewayValidationError(GatewayError):
    """Raised when incoming path or gateway request validation fails."""

    def __init__(
        self,
        message: str = "Invalid request parameters.",
        status_code: int = status.HTTP_400_BAD_REQUEST,
    ) -> None:
        super().__init__(
            status_code=status_code,
            error_code="VALIDATION_ERROR",
            message=message,
        )


class ServiceNotFoundError(GatewayError):
    """Raised when no matching route or resource is found."""

    def __init__(self, message: str = "The requested endpoint was not found.") -> None:
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code="NOT_FOUND",
            message=message,
        )


class ServiceConfigurationError(GatewayError):
    """Raised when a required service configuration entry is missing or invalid."""

    def __init__(
        self,
        message: str = "The requested service is currently unavailable due to configuration.",
    ) -> None:
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            error_code="SERVICE_UNAVAILABLE",
            message=message,
        )


class ServiceTimeoutError(GatewayError):
    """Raised when a downstream microservice exceeds the synchronous request timeout."""

    def __init__(
        self,
        message: str = "The upstream service timed out while processing the request.",
    ) -> None:
        super().__init__(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            error_code="SERVICE_TIMEOUT",
            message=message,
        )


class ServiceUnavailableError(GatewayError):
    """Raised when a downstream microservice cannot be reached."""

    def __init__(
        self,
        message: str = "The requested service is currently unavailable.",
        status_code: int = status.HTTP_503_SERVICE_UNAVAILABLE,
    ) -> None:
        super().__init__(
            status_code=status_code,
            error_code="SERVICE_UNAVAILABLE",
            message=message,
        )


def resolve_request_id(request: Request | None = None) -> str:
    """Retrieve the active request correlation ID from state or contextvars."""
    if request is not None:
        state_id = getattr(request.state, "request_id", None)
        if isinstance(state_id, str) and state_id:
            return state_id
    ctx_id = request_id_ctx.get("-")
    if ctx_id and ctx_id != "-":
        return ctx_id
    return str(uuid.uuid4())


def _sanitize_error_message(message: str, fallback: str) -> str:
    """Ensure error messages returned to callers do not leak URLs or stack traces."""
    cleaned = " ".join(message.strip().split())
    if not cleaned:
        return fallback
    if "Traceback (most recent call last)" in cleaned:
        return fallback
    cleaned = _INTERNAL_URL_RE.sub("[redacted-url]", cleaned)
    return cleaned[:300]


def _default_error_for_status(status_code: int) -> tuple[str, str]:
    """Map an HTTP status code to a standard Gateway error category and message."""
    mapping: dict[int, tuple[str, str]] = {
        400: ("VALIDATION_ERROR", "The request could not be validated."),
        401: ("UNAUTHORIZED", "Authentication is required."),
        403: ("FORBIDDEN", "You do not have permission to perform this action."),
        404: ("NOT_FOUND", "The requested resource was not found."),
        405: ("VALIDATION_ERROR", "HTTP method is not allowed for this endpoint."),
        409: ("CONFLICT", "The request conflicts with the current resource state."),
        422: ("VALIDATION_ERROR", "Request validation failed."),
        429: ("RATE_LIMITED", "Too many requests."),
        500: ("INTERNAL_SERVER_ERROR", "An unexpected internal server error occurred."),
        502: ("SERVICE_UNAVAILABLE", "Upstream service communication failed."),
        503: ("SERVICE_UNAVAILABLE", "The requested service is currently unavailable."),
        504: ("SERVICE_TIMEOUT", "The upstream service timed out while processing the request."),
    }
    if status_code in mapping:
        return mapping[status_code]
    if 400 <= status_code < 500:
        return ("CLIENT_ERROR", "The request could not be processed.")
    return ("INTERNAL_SERVER_ERROR", "An unexpected internal server error occurred.")


def build_error_response(
    *,
    status_code: int,
    error_code: str,
    message: str,
    request_id: str,
) -> JSONResponse:
    """Construct the standardized JSON error response envelope."""
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "error": {
                "code": error_code,
                "message": message,
            },
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id},
    )


def build_downstream_error_response(
    downstream_response: httpx.Response,
    request_id: str,
    service_name: str,
) -> JSONResponse:
    """Normalize downstream 4xx/5xx responses safely without leaking internals."""
    raw_status = downstream_response.status_code

    # Sanitize all downstream 5xx responses to protect internal traces/hostnames.
    if raw_status >= 500:
        if raw_status == status.HTTP_504_GATEWAY_TIMEOUT:
            out_status = status.HTTP_504_GATEWAY_TIMEOUT
            code, message = _default_error_for_status(504)
        elif raw_status == status.HTTP_503_SERVICE_UNAVAILABLE:
            out_status = status.HTTP_503_SERVICE_UNAVAILABLE
            code, message = _default_error_for_status(503)
        else:
            out_status = status.HTTP_502_BAD_GATEWAY
            code = "INTERNAL_SERVER_ERROR"
            message = "An upstream service error occurred."

        logger.warning(
            "Downstream service returned server error",
            extra={
                "event": "downstream_server_error",
                "target_service": service_name,
                "status_code": raw_status,
                "error_code": code,
            },
        )
        return build_error_response(
            status_code=out_status,
            error_code=code,
            message=message,
            request_id=request_id,
        )

    # Preserve meaningful 4xx status codes while normalizing the error payload.
    default_code, default_message = _default_error_for_status(raw_status)
    code = default_code
    message = default_message

    content_type = downstream_response.headers.get("content-type", "")
    if "application/json" in content_type.lower():
        try:
            payload: Any = downstream_response.json()
        except ValueError:
            payload = None

        if isinstance(payload, dict):
            err_obj = payload.get("error")
            if isinstance(err_obj, dict):
                raw_code = err_obj.get("code")
                raw_msg = err_obj.get("message")
                if isinstance(raw_code, str) and _SAFE_CODE_RE.match(raw_code.strip()):
                    code = raw_code.strip()
                if isinstance(raw_msg, str):
                    message = _sanitize_error_message(raw_msg, default_message)
            elif isinstance(payload.get("detail"), str):
                message = _sanitize_error_message(payload["detail"], default_message)
            elif isinstance(payload.get("message"), str):
                message = _sanitize_error_message(payload["message"], default_message)

    logger.info(
        "Downstream service returned client error",
        extra={
            "event": "downstream_client_error",
            "target_service": service_name,
            "status_code": raw_status,
            "error_code": code,
        },
    )
    return build_error_response(
        status_code=raw_status,
        error_code=code,
        message=message,
        request_id=request_id,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register centralized exception handlers on the FastAPI application."""

    @app.exception_handler(GatewayError)
    async def handle_gateway_error(request: Request, exc: GatewayError) -> JSONResponse:
        request_id = resolve_request_id(request)
        logger.warning(
            "Gateway error handled",
            extra={
                "event": "gateway_error",
                "method": request.method,
                "path": request.url.path,
                "status_code": exc.status_code,
                "error_code": exc.error_code,
            },
        )
        return build_error_response(
            status_code=exc.status_code,
            error_code=exc.error_code,
            message=exc.message,
            request_id=request_id,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        request_id = resolve_request_id(request)
        logger.info(
            "Request validation failed",
            extra={
                "event": "request_validation_error",
                "method": request.method,
                "path": request.url.path,
                "status_code": status.HTTP_422_UNPROCESSABLE_ENTITY,
                "error_code": "VALIDATION_ERROR",
            },
        )
        return build_error_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            error_code="VALIDATION_ERROR",
            message="Request validation failed.",
            request_id=request_id,
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        request_id = resolve_request_id(request)
        error_code, default_message = _default_error_for_status(exc.status_code)
        detail = exc.detail if isinstance(exc.detail, str) else default_message
        message = _sanitize_error_message(detail, default_message)
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            message = "The requested endpoint was not found."
        return build_error_response(
            status_code=exc.status_code,
            error_code=error_code,
            message=message,
            request_id=request_id,
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = resolve_request_id(request)
        logger.error(
            "Unhandled Gateway exception",
            exc_info=(type(exc), exc, exc.__traceback__),
            extra={
                "event": "unhandled_exception",
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": status.HTTP_500_INTERNAL_SERVER_ERROR,
                "error_code": "INTERNAL_SERVER_ERROR",
            },
        )
        return build_error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="INTERNAL_SERVER_ERROR",
            message="An unexpected internal server error occurred.",
            request_id=request_id,
        )

