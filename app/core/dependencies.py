"""
Centralized dependency injection functions.

This module provides reusable dependencies that can be used across
the application for common functionality like authentication,
database access, and more.
"""

from fastapi import Depends, Header, Request

from app.core.auth import authenticate_api_key
from app.core.config import Settings, get_settings


# Settings dependency
def get_app_settings() -> Settings:
    """
    Get application settings.

    Returns:
        Settings: Application settings
    """
    return get_settings()


# Authentication dependencies
async def get_api_key_dependency(api_key: str = Depends(authenticate_api_key)) -> str:
    """
    Get the current API key.

    This is an alias for authenticate_api_key for backward compatibility.

    Args:
        api_key: The API key from authenticate_api_key

    Returns:
        str: The current API key
    """
    return api_key


async def get_optional_api_key(request: Request, x_api_key: str | None = Header(None, alias="X-API-Key")) -> str | None:
    """
    Get the API key if provided, but don't require it.

    Args:
        request: The request object
        x_api_key: The API key from the header

    Returns:
        Optional[str]: The API key if provided and valid, None otherwise
    """
    if not x_api_key:
        return None

    try:
        return await authenticate_api_key(x_api_key, request)
    except:
        return None


# -----------------------------------------------------------------------------
# Service dependencies for testability
# -----------------------------------------------------------------------------
from app.core.cache_manager import CacheManager, cache_manager
from app.core.http_client import HTTPClientManager, get_http_client_manager


def get_http_client_dependency() -> HTTPClientManager:
    """
    Dependency provider for HTTPClientManager.

    Returns:
        HTTPClientManager: HTTP client manager instance
    """
    return get_http_client_manager()


def get_cache_manager_dependency() -> CacheManager:
    """
    Dependency provider for CacheManager.

    Returns:
        CacheManager: Cache manager instance
    """
    return cache_manager


class ServiceDependencies:
    """
    Container class for service dependencies.

    Can be used to override dependencies in tests by creating
    a test instance with mocked dependencies.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        http_client: HTTPClientManager | None = None,
        cache: CacheManager | None = None,
    ):
        self._settings = settings
        self._http_client = http_client
        self._cache = cache

    @property
    def settings(self) -> Settings:
        """Get settings instance."""
        return self._settings or get_settings()

    @property
    def http_client(self) -> HTTPClientManager:
        """Get HTTP client manager instance."""
        return self._http_client or get_http_client_manager()

    @property
    def cache(self) -> CacheManager:
        """Get cache manager instance."""
        return self._cache or cache_manager


_default_dependencies: ServiceDependencies | None = None


def get_service_dependencies() -> ServiceDependencies:
    """
    Get the service dependencies container.

    Returns:
        ServiceDependencies: Container with service dependencies
    """
    global _default_dependencies
    if _default_dependencies is None:
        _default_dependencies = ServiceDependencies()
    return _default_dependencies


def set_service_dependencies(deps: ServiceDependencies) -> None:
    """
    Set custom service dependencies (primarily for testing).

    Args:
        deps: Custom dependencies container
    """
    global _default_dependencies
    _default_dependencies = deps


def reset_service_dependencies() -> None:
    """Reset to default dependencies."""
    global _default_dependencies
    _default_dependencies = None
