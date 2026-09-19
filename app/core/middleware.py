"""
Custom middleware for the Headwater application.

This module provides middleware for CORS, logging, security headers,
and other cross-cutting concerns.
"""
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
import time
import logging
import uuid
from typing import Callable, List, Optional, Dict, Any

from app.core.config import get_settings, Settings


# Configure logger
logger = logging.getLogger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware for logging request and response details.
    
    This middleware logs information about each request and response,
    including method, path, status code, and processing time.
    """
    
    async def dispatch(
        self, request: Request, call_next: Callable
    ) -> Response:
        """
        Process the request and log details.
        
        Args:
            request: The incoming request
            call_next: The next middleware or route handler
            
        Returns:
            Response: The response from the next middleware or route handler
        """
        # Generate a unique request ID if not already present
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        
        # Store the request ID in the request state for later use
        request.state.request_id = request_id
        
        # Log the request
        logger.info(
            f"Request started",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "query_params": str(request.query_params),
                "client_host": request.client.host if request.client else None,
                "user_agent": request.headers.get("User-Agent"),
            }
        )
        
        # Record the start time
        start_time = time.time()
        
        # Process the request
        try:
            response = await call_next(request)
            
            # Calculate processing time
            process_time = time.time() - start_time
            
            # Add the request ID to the response headers
            response.headers["X-Request-ID"] = request_id
            
            # Log the response
            logger.info(
                f"Request completed",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "process_time_ms": round(process_time * 1000, 2),
                }
            )
            
            return response
        except Exception as e:
            # Calculate processing time
            process_time = time.time() - start_time
            
            # Log the error
            logger.exception(
                f"Request failed",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "error": str(e),
                    "process_time_ms": round(process_time * 1000, 2),
                }
            )
            
            # Re-raise the exception
            raise


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Middleware for adding security headers to responses.
    
    This middleware adds various security headers to responses to
    improve the security of the application.
    """
    
    async def dispatch(
        self, request: Request, call_next: Callable
    ) -> Response:
        """
        Process the request and add security headers to the response.
        
        Args:
            request: The incoming request
            call_next: The next middleware or route handler
            
        Returns:
            Response: The response with added security headers
        """
        # Process the request
        response = await call_next(request)
        
        # Add security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        
        # Add Content-Security-Policy header for production
        settings = get_settings()
        if settings.ENVIRONMENT == "production":
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; "
                "font-src 'self'; "
                "connect-src 'self'"
            )
        
        # Add Strict-Transport-Security header for production
        if settings.ENVIRONMENT == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        
        return response


class CORSConfigurationError(RuntimeError):
    """Raised when the configured CORS policy is unsafe and must not be served."""


def _is_wildcard_origins(origins: Any) -> bool:
    """
    Return True when the configured origin list is (or contains) a wildcard.

    Written defensively so that non-list settings objects (e.g. mocks used in
    tests) never raise from a membership check.
    """
    try:
        return "*" in origins
    except TypeError:
        return False


def resolve_cors_policy(settings: Settings) -> Dict[str, Any]:
    """
    Resolve a safe CORS policy from settings.

    ``allow_origins=["*"]`` combined with ``allow_credentials=True`` is invalid
    per the CORS specification: Starlette responds by *reflecting* the request
    ``Origin`` together with ``Access-Control-Allow-Credentials: true``, which
    lets any site on the internet make credentialed cross-origin calls to this
    API. Two rules follow from that:

    1. In production an explicit origin allow-list is mandatory. A wildcard is
       refused outright rather than silently downgraded.
    2. Outside production the wildcard is still accepted for developer
       convenience, but credentials are forcibly disabled so the invalid
       combination can never be served.

    Args:
        settings: The application settings

    Returns:
        Dict[str, Any]: kwargs for ``CORSMiddleware``

    Raises:
        CORSConfigurationError: If a wildcard origin is configured in production
    """
    origins = settings.CORS_ORIGINS
    environment = getattr(settings, "ENVIRONMENT", "development")
    wildcard = _is_wildcard_origins(origins)

    if wildcard and environment == "production":
        raise CORSConfigurationError(
            "CORS_ORIGINS must list explicit origins in production. "
            'The default wildcard ("*") cannot be combined with credentialed '
            "requests; set CORS_ORIGINS to a comma-separated allow-list of "
            "trusted origins (e.g. https://app.example.com)."
        )

    if wildcard:
        logger.warning(
            "CORS is configured with a wildcard origin (\"*\"); "
            "credentials are disabled for cross-origin requests. "
            "Set CORS_ORIGINS to an explicit allow-list to enable them."
        )

    return {
        "allow_origins": origins,
        # Never combine credentials with a wildcard origin.
        "allow_credentials": not wildcard,
        "allow_methods": settings.CORS_METHODS,
        "allow_headers": settings.CORS_HEADERS,
    }


def setup_middleware(app: FastAPI, settings: Optional[Settings] = None) -> None:
    """
    Set up middleware for the FastAPI application.

    Args:
        app: The FastAPI application
        settings: Optional settings instance
    """
    if settings is None:
        settings = get_settings()

    # Add CORS middleware with a policy that can never pair "*" with credentials
    app.add_middleware(CORSMiddleware, **resolve_cors_policy(settings))

    # Add trusted host middleware for production
    if settings.ENVIRONMENT == "production":
        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=["api.headwater.com", "headwater.com", "localhost"]
        )
    
    # Add GZip compression middleware
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    
    # Add security headers middleware
    app.add_middleware(SecurityHeadersMiddleware)
    
    # Add request logging middleware
    app.add_middleware(RequestLoggingMiddleware)
