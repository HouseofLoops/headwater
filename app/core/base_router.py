from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import get_api_key as authenticate_api_key


class BaseRouter:
    """
    Base router class that provides common functionality for all API routers.

    Features:
    - Auto-derives service_name from prefix if not provided
    - Supports OpenAPI responses documentation
    - Provides RFC7807 compliant error responses
    - Centralizes authentication
    """

    def __init__(
        self, prefix: str, service_name: str | None = None, responses: dict[int, dict] | None = None, **kwargs
    ):
        """
        Initialize a new BaseRouter.

        Args:
            prefix: URL prefix for all routes (e.g., "/google-trends")
            service_name: Optional name for the service. If not provided, derived from prefix.
            responses: Optional dict of status codes to response models for OpenAPI docs.
            **kwargs: Additional arguments to pass to the underlying APIRouter.
        """
        # Validate and normalize prefix
        if not prefix.startswith("/"):
            prefix = f"/{prefix}"

        # Extract service name from prefix
        extracted_service_name = self._extract_service_name(prefix)

        # Use provided service_name or extracted one
        self.service_name = service_name or extracted_service_name

        # Validate consistency between extracted and provided service_name
        if service_name and service_name != extracted_service_name:
            import logging

            logging.warning(
                f"Provided service_name '{service_name}' differs from extracted "
                f"service_name '{extracted_service_name}' from prefix '{prefix}'"
            )

        # Create the underlying FastAPI router
        self.router = APIRouter(
            prefix=prefix,
            tags=[self.service_name],
            responses=responses or self._default_responses(),
            dependencies=[Depends(authenticate_api_key)],
            **kwargs,
        )

    def _extract_service_name(self, prefix: str) -> str:
        """
        Extract service name from the URL prefix.

        Example:
            "/google-trends" -> "google-trends"
            "/youtube-transcripts" -> "youtube-transcripts"
            "/api/v1/google-news" -> "google-news"

        Args:
            prefix: URL prefix string

        Returns:
            Extracted service name
        """
        # Remove leading slash if present
        if prefix.startswith("/"):
            prefix = prefix[1:]

        # Split on slashes
        parts = prefix.split("/")
        if not parts:
            raise ValueError(f"Cannot extract service name from prefix: {prefix}")

        # If the prefix is like "/api/v1/google-news", take the last segment
        if len(parts) > 1 and parts[0] == "api" and parts[1].startswith("v"):
            service_name = parts[2] if len(parts) > 2 else parts[0]
        else:
            service_name = parts[0]

        # Validate that we have a non-empty service name
        if not service_name:
            raise ValueError(f"Cannot extract service name from prefix: {prefix}")

        return service_name

    def _default_responses(self) -> dict[int, dict]:
        """
        Provide default response schemas for common HTTP status codes.

        Returns:
            Dictionary of status codes to response schemas
        """
        return {
            400: {
                "description": "Bad Request",
                "content": {
                    "application/problem+json": {
                        "example": self._create_error_detail(
                            status=400,
                            title="Bad Request",
                            detail="Invalid request parameters",
                            type="validation_error",
                        )
                    }
                },
            },
            401: {
                "description": "Unauthorized",
                "content": {
                    "application/problem+json": {
                        "example": self._create_error_detail(
                            status=401,
                            title="Unauthorized",
                            detail="Authentication required",
                            type="authentication_error",
                        )
                    }
                },
            },
            403: {
                "description": "Forbidden",
                "content": {
                    "application/problem+json": {
                        "example": self._create_error_detail(
                            status=403, title="Forbidden", detail="Invalid API key", type="authorization_error"
                        )
                    }
                },
            },
            404: {
                "description": "Not Found",
                "content": {
                    "application/problem+json": {
                        "example": self._create_error_detail(
                            status=404,
                            title="Not Found",
                            detail="The requested resource was not found",
                            type="not_found",
                        )
                    }
                },
            },
            422: {
                "description": "Unprocessable Entity",
                "content": {
                    "application/problem+json": {
                        "example": self._create_error_detail(
                            status=422, title="Unprocessable Entity", detail="Validation error", type="validation_error"
                        )
                    }
                },
            },
            500: {
                "description": "Internal Server Error",
                "content": {
                    "application/problem+json": {
                        "example": self._create_error_detail(
                            status=500,
                            title="Internal Server Error",
                            detail="An unexpected error occurred",
                            type="server_error",
                        )
                    }
                },
            },
        }

    def _create_error_detail(
        self, status: int, title: str, detail: str, type: str, instance: str | None = None, **kwargs
    ) -> dict:
        """
        Create an RFC7807 compliant error response.

        Args:
            status: HTTP status code
            title: Human-readable title of the error
            detail: Specific details about the error
            type: Error type identifier
            instance: URI of the specific occurrence of the error
            **kwargs: Additional fields to include in the error response

        Returns:
            Dictionary with RFC7807 compliant error details
        """
        # Ensure type is a proper URI
        if not type.startswith(("http://", "https://")):
            type = f"https://headwater.com/problems/{type}"

        error = {"type": type, "title": title, "status": status, "detail": detail}

        if instance:
            error["instance"] = instance

        # Add any additional fields
        error.update(kwargs)

        return error

    def raise_http_exception(
        self,
        status_code: int,
        detail: str,
        title: str | None = None,
        type: str | None = None,
        instance: str | None = None,
        headers: dict[str, str] | None = None,
        **kwargs,
    ) -> None:
        """
        Raise an HTTPException with RFC7807 compliant error details.

        Args:
            status_code: HTTP status code
            detail: Specific details about the error
            title: Human-readable title of the error (defaults to status code description)
            type: Error type identifier (defaults to generic type based on status code)
            instance: URI of the specific occurrence of the error
            headers: Optional HTTP headers to include in the response
            **kwargs: Additional fields to include in the error response

        Raises:
            HTTPException: With RFC7807 compliant error details
        """
        # Default title based on status code
        if not title:
            titles = {
                400: "Bad Request",
                401: "Unauthorized",
                403: "Forbidden",
                404: "Not Found",
                422: "Unprocessable Entity",
                500: "Internal Server Error",
            }
            title = titles.get(status_code, "Error")

        # Default type based on status code
        if not type:
            types = {
                400: "validation_error",
                401: "authentication_error",
                403: "authorization_error",
                404: "not_found",
                422: "validation_error",
                500: "server_error",
            }
            type = types.get(status_code, "error")

        # Create RFC7807 error detail
        error_detail = self._create_error_detail(
            status=status_code, title=title, detail=detail, type=type, instance=instance, **kwargs
        )

        # Set Content-Type header for RFC7807
        if not headers:
            headers = {}
        headers["Content-Type"] = "application/problem+json"

        raise HTTPException(status_code=status_code, detail=error_detail, headers=headers)

    # Convenience methods for common error types

    def raise_validation_error(self, detail: str, field: str | None = None, **kwargs) -> None:
        """Raise a 400 Bad Request error with validation details."""
        extra = {"field": field} if field else {}
        extra.update(kwargs)
        self.raise_http_exception(400, detail, type="validation_error", **extra)

    def raise_not_found_error(self, resource_type: str, identifier: Any, **kwargs) -> None:
        """Raise a 404 Not Found error with resource details."""
        detail = f"{resource_type} with identifier '{identifier}' not found"
        self.raise_http_exception(404, detail, type="not_found", **kwargs)

    def raise_internal_error(self, detail: str | None = None, **kwargs) -> None:
        """Raise a 500 Internal Server Error."""
        self.raise_http_exception(500, detail or "An unexpected error occurred", type="server_error", **kwargs)

    # Delegate HTTP method decorators to the underlying router

    def get(self, *args, **kwargs):
        """Register a GET route."""
        return self.router.get(*args, **kwargs)

    def post(self, *args, **kwargs):
        """Register a POST route."""
        return self.router.post(*args, **kwargs)

    def put(self, *args, **kwargs):
        """Register a PUT route."""
        return self.router.put(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Register a DELETE route."""
        return self.router.delete(*args, **kwargs)

    def patch(self, *args, **kwargs):
        """Register a PATCH route."""
        return self.router.patch(*args, **kwargs)

    def options(self, *args, **kwargs):
        """Register an OPTIONS route."""
        return self.router.options(*args, **kwargs)

    def head(self, *args, **kwargs):
        """Register a HEAD route."""
        return self.router.head(*args, **kwargs)

    def trace(self, *args, **kwargs):
        """Register a TRACE route."""
        return self.router.trace(*args, **kwargs)

    # Additional router methods

    def include_router(self, *args, **kwargs):
        """Include another router."""
        return self.router.include_router(*args, **kwargs)

    def routes(self):
        """Get all routes registered with this router."""
        return self.router.routes

    # Make the router instance available for FastAPI's include_router
    def __call__(self):
        """Return the underlying router instance."""
        return self.router
