"""
Custom exceptions and error handling utilities.

This module provides custom exception classes and utilities for
standardized error handling across the application.
"""

import logging
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

# Configure logger
logger = logging.getLogger(__name__)


class HeadwaterException(Exception):
    """
    Base exception class for all Headwater application exceptions.

    This class provides a common interface for all application-specific
    exceptions, with support for RFC7807 Problem Details.
    """

    status_code: int = 500
    detail: str = "An unexpected error occurred"
    error_type: str = "server_error"
    title: str = "Internal Server Error"
    headers: dict[str, str] | None = None

    def __init__(
        self,
        detail: str | None = None,
        status_code: int | None = None,
        error_type: str | None = None,
        title: str | None = None,
        headers: dict[str, str] | None = None,
        **kwargs,
    ):
        """
        Initialize the exception.

        Args:
            detail: Detailed error message
            status_code: HTTP status code
            error_type: Error type identifier
            title: Human-readable title
            headers: HTTP headers to include in the response
            **kwargs: Additional fields to include in the error response
        """
        self.detail = detail or self.detail
        self.status_code = status_code or self.status_code
        self.error_type = error_type or self.error_type
        self.title = title or self.title
        self.headers = headers or self.headers or {}
        self.extra = kwargs

        # Set Content-Type header for RFC7807
        if "Content-Type" not in self.headers:
            self.headers["Content-Type"] = "application/problem+json"

        super().__init__(self.detail)

    def to_dict(self) -> dict[str, Any]:
        """
        Convert the exception to a dictionary for the response.

        Returns:
            Dict[str, Any]: Dictionary representation of the exception
        """
        error_dict = {
            "type": f"https://headwater.com/problems/{self.error_type}",
            "title": self.title,
            "status": self.status_code,
            "detail": self.detail,
        }

        # Add any additional fields
        error_dict.update(self.extra)

        return error_dict


# 400 Bad Request Exceptions


class ValidationError(HeadwaterException):
    """Exception for validation errors."""

    status_code = 400
    detail = "Validation error"
    error_type = "validation_error"
    title = "Bad Request"


class InvalidParameterError(ValidationError):
    """Exception for invalid parameter errors."""

    detail = "Invalid parameter"
    error_type = "invalid_parameter"


class MissingParameterError(ValidationError):
    """Exception for missing parameter errors."""

    detail = "Missing required parameter"
    error_type = "missing_parameter"


# 401 Unauthorized Exceptions


class AuthenticationError(HeadwaterException):
    """Exception for authentication errors."""

    status_code = 401
    detail = "Authentication required"
    error_type = "authentication_error"
    title = "Unauthorized"

    def __init__(self, *args, **kwargs):
        """Initialize with WWW-Authenticate header."""
        super().__init__(*args, **kwargs)
        self.headers["WWW-Authenticate"] = "Bearer"


class InvalidCredentialsError(AuthenticationError):
    """Exception for invalid credentials."""

    detail = "Invalid credentials"
    error_type = "invalid_credentials"


# 403 Forbidden Exceptions


class PermissionDeniedError(HeadwaterException):
    """Exception for permission denied errors."""

    status_code = 403
    detail = "Permission denied"
    error_type = "permission_denied"
    title = "Forbidden"


class RateLimitExceededError(HeadwaterException):
    """Exception for rate limit exceeded errors."""

    status_code = 429
    detail = "Rate limit exceeded"
    error_type = "rate_limit_exceeded"
    title = "Too Many Requests"


# 404 Not Found Exceptions


class NotFoundError(HeadwaterException):
    """Exception for not found errors."""

    status_code = 404
    detail = "Resource not found"
    error_type = "not_found"
    title = "Not Found"


# 409 Conflict Exceptions


class ConflictError(HeadwaterException):
    """Exception for conflict errors."""

    status_code = 409
    detail = "Resource conflict"
    error_type = "conflict"
    title = "Conflict"


class ResourceExistsError(ConflictError):
    """Exception for resource already exists errors."""

    detail = "Resource already exists"
    error_type = "resource_exists"


# 500 Server Error Exceptions


class ServerError(HeadwaterException):
    """Exception for server errors."""

    status_code = 500
    detail = "Internal server error"
    error_type = "server_error"
    title = "Internal Server Error"


class DatabaseError(ServerError):
    """Exception for database errors."""

    detail = "Database error"
    error_type = "database_error"


class ExternalServiceError(ServerError):
    """Exception for external service errors."""

    detail = "External service error"
    error_type = "external_service_error"


class ServiceUnavailableError(HeadwaterException):
    """Exception for service unavailable errors."""

    status_code = 503
    detail = "Service unavailable"
    error_type = "service_unavailable"
    title = "Service Unavailable"


# Exception handlers


async def headwater_exception_handler(request: Request, exc: HeadwaterException) -> JSONResponse:
    """
    Handle HeadwaterException instances.

    Args:
        request: The request that caused the exception
        exc: The exception instance

    Returns:
        JSONResponse: RFC7807 compliant error response
    """
    # Log the exception
    logger.error(
        f"HeadwaterException: {exc.detail}",
        extra={"status_code": exc.status_code, "error_type": exc.error_type, "path": request.url.path},
    )

    # Return RFC7807 response
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict(), headers=exc.headers)


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """
    Handle HTTPException instances and convert to RFC7807 format.

    Args:
        request: The request that caused the exception
        exc: The exception instance

    Returns:
        JSONResponse: RFC7807 compliant error response
    """
    # Map status code to error type and title
    error_types = {
        400: ("validation_error", "Bad Request"),
        401: ("authentication_error", "Unauthorized"),
        403: ("permission_denied", "Forbidden"),
        404: ("not_found", "Not Found"),
        409: ("conflict", "Conflict"),
        422: ("validation_error", "Unprocessable Entity"),
        429: ("rate_limit_exceeded", "Too Many Requests"),
        500: ("server_error", "Internal Server Error"),
        503: ("service_unavailable", "Service Unavailable"),
    }

    error_type, title = error_types.get(exc.status_code, ("error", f"HTTP Error {exc.status_code}"))

    # Create RFC7807 response
    content = {
        "type": f"https://headwater.com/problems/{error_type}",
        "title": title,
        "status": exc.status_code,
        "detail": str(exc.detail),
    }

    # Set headers
    headers = exc.headers or {}
    if "Content-Type" not in headers:
        headers["Content-Type"] = "application/problem+json"

    # Log the exception
    logger.error(f"HTTPException: {exc.detail}", extra={"status_code": exc.status_code, "path": request.url.path})

    return JSONResponse(status_code=exc.status_code, content=content, headers=headers)


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Handle unhandled exceptions and convert to RFC7807 format.

    Args:
        request: The request that caused the exception
        exc: The exception instance

    Returns:
        JSONResponse: RFC7807 compliant error response
    """
    # Log the exception with traceback
    logger.exception(f"Unhandled exception: {exc!s}", extra={"path": request.url.path})

    # Create RFC7807 response
    content = {
        "type": "https://headwater.com/problems/server_error",
        "title": "Internal Server Error",
        "status": 500,
        "detail": "An unexpected error occurred",
    }

    return JSONResponse(status_code=500, content=content, headers={"Content-Type": "application/problem+json"})


# Helper functions


def configure_exception_handlers(app):
    """
    Configure exception handlers for a FastAPI application.

    Args:
        app: The FastAPI application
    """
    app.add_exception_handler(HeadwaterException, headwater_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)


# =============================================================================
# Convenience Exception Factories
# =============================================================================


class ProxyConnectionError(ExternalServiceError):
    """Exception for proxy connection failures."""

    status_code = 502
    detail = "Proxy connection failed"
    error_type = "proxy_error"
    title = "Bad Gateway"


class IPBlockedError(ServiceUnavailableError):
    """Exception for IP blocking by external services."""

    detail = "Service is temporarily blocking requests"
    error_type = "ip_blocked"


def raise_not_found(resource: str, identifier: Any = None) -> None:
    """Raise a NotFoundError with formatted message."""
    detail = f"{resource} not found"
    if identifier:
        detail = f"{resource} '{identifier}' not found"
    raise NotFoundError(detail=detail)


def raise_validation_error(message: str, field: str | None = None) -> None:
    """Raise a ValidationError with optional field information."""
    extra = {"field": field} if field else {}
    raise ValidationError(detail=message, **extra)


def raise_service_unavailable(service: str, reason: str = "temporarily unavailable") -> None:
    """Raise a ServiceUnavailableError for external service issues."""
    raise ServiceUnavailableError(detail=f"{service} is {reason}. Please try again later.")


def raise_proxy_error(service: str = "Service") -> None:
    """Raise a ProxyConnectionError."""
    raise ProxyConnectionError(detail=f"{service} proxy connection failed. Please check configuration.")


def raise_ip_blocked(service: str = "Service") -> None:
    """Raise an IPBlockedError."""
    raise IPBlockedError(detail=f"{service} is temporarily blocking requests. Please try again later.")
