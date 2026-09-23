"""
Health check utilities for the Headwater application.

This module provides functions to check the health of various
dependencies like Redis, external APIs, and system resources.

Note: there is no database health check. The application has no database
layer (``app/core/database.py`` was removed in the Phase 4 dependency
cleanup); the previous check imported SQLAlchemy/asyncpg, neither of which
was ever a declared dependency, and it ``await``-ed an async generator, so
it could never have succeeded.
"""

import asyncio
import logging
import time
from typing import Any

import httpx
from fastapi import Depends

from app.core.config import Settings, get_settings
from app.core.exceptions import ServiceUnavailableError

# Configure logger
logger = logging.getLogger(__name__)


async def check_redis_connection() -> dict[str, Any]:
    """
    Check the Redis connection.

    Returns:
        Dict[str, Any]: Status information

    Raises:
        ServiceUnavailableError: If Redis is unavailable
    """
    settings = get_settings()

    if not settings.REDIS_URL:
        return {"status": "skipped", "message": "Redis URL not configured"}

    try:
        # Import here to avoid circular imports
        # CacheManager has no _get_redis_client; it reaches Redis through the
        # module-level _get_redis_manager() singleton. Calling the non-existent
        # method raised AttributeError on every run, so the Redis health check
        # has always reported failure regardless of Redis actually being up.
        from app.core.cache_manager import _get_redis_manager

        manager = await _get_redis_manager()
        redis_client = await manager.get_client() if manager and manager.is_available else None

        if not redis_client:
            return {"status": "skipped", "message": "Redis client not initialized"}

        # Ping Redis
        start_time = time.time()
        result = await redis_client.ping()
        response_time = time.time() - start_time

        # Check the result
        if result:
            return {
                "status": "healthy",
                "message": "Redis connection successful",
                "response_time_ms": round(response_time * 1000, 2),
            }
        else:
            raise ServiceUnavailableError(detail="Redis ping failed", error_type="redis_unavailable")
    except ImportError:
        return {"status": "skipped", "message": "Redis module not available"}
    except Exception as e:
        logger.exception("Redis health check failed")
        raise ServiceUnavailableError(detail=f"Redis connection failed: {e!s}", error_type="redis_unavailable")


async def check_external_apis() -> dict[str, Any]:
    """
    Check external APIs.

    Returns:
        Dict[str, Any]: Status information

    Raises:
        ServiceUnavailableError: If any external API is unavailable
    """
    apis_to_check = [
        # Add external APIs to check here
        # For example:
        # {"name": "Google", "url": "https://www.google.com", "timeout": 5}
    ]

    results = {}

    async with httpx.AsyncClient() as client:
        for api in apis_to_check:
            name = api["name"]
            url = api["url"]
            timeout = api.get("timeout", 5)

            try:
                start_time = time.time()
                response = await client.get(url, timeout=timeout)
                response_time = time.time() - start_time

                if response.status_code < 400:
                    results[name] = {
                        "status": "healthy",
                        "message": f"{name} API is available",
                        "response_time_ms": round(response_time * 1000, 2),
                        "status_code": response.status_code,
                    }
                else:
                    results[name] = {
                        "status": "unhealthy",
                        "message": f"{name} API returned error status",
                        "response_time_ms": round(response_time * 1000, 2),
                        "status_code": response.status_code,
                    }
            except Exception as e:
                logger.warning(f"API health check failed for {name}: {e!s}")
                results[name] = {"status": "unhealthy", "message": f"{name} API is unavailable: {e!s}", "error": str(e)}

    # Check if any API is unhealthy
    unhealthy_apis = [name for name, result in results.items() if result["status"] == "unhealthy"]

    if unhealthy_apis:
        logger.warning(f"Unhealthy APIs: {', '.join(unhealthy_apis)}")

    return {
        "status": "healthy" if not unhealthy_apis else "degraded",
        "message": "All external APIs are available"
        if not unhealthy_apis
        else f"Some external APIs are unavailable: {', '.join(unhealthy_apis)}",
        "apis": results,
    }


async def check_system_resources() -> dict[str, Any]:
    """
    Check system resources.

    Returns:
        Dict[str, Any]: Status information
    """
    try:
        import psutil

        # Get CPU usage
        cpu_percent = psutil.cpu_percent(interval=0.1)

        # Get memory usage
        memory = psutil.virtual_memory()
        memory_percent = memory.percent

        # Get disk usage
        disk = psutil.disk_usage("/")
        disk_percent = disk.percent

        return {
            "status": "healthy",
            "message": "System resources checked",
            "cpu": {"percent": cpu_percent, "status": "healthy" if cpu_percent < 90 else "warning"},
            "memory": {"percent": memory_percent, "status": "healthy" if memory_percent < 90 else "warning"},
            "disk": {"percent": disk_percent, "status": "healthy" if disk_percent < 90 else "warning"},
        }
    except ImportError:
        return {"status": "skipped", "message": "psutil module not available"}
    except Exception as e:
        logger.warning(f"System resources check failed: {e!s}")
        return {"status": "unknown", "message": f"System resources check failed: {e!s}"}


async def check_health(include_details: bool = False, settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """
    Check the health of all dependencies.

    Args:
        include_details: Whether to include detailed information
        settings: Application settings

    Returns:
        Dict[str, Any]: Health status information
    """
    # Run all health checks concurrently
    checks = await asyncio.gather(
        check_redis_connection(), check_external_apis(), check_system_resources(), return_exceptions=True
    )

    # Process the results
    results = {
        "redis": checks[0]
        if not isinstance(checks[0], Exception)
        else {"status": "unhealthy", "message": str(checks[0])},
        "external_apis": checks[1]
        if not isinstance(checks[1], Exception)
        else {"status": "unhealthy", "message": str(checks[1])},
        "system": checks[2]
        if not isinstance(checks[2], Exception)
        else {"status": "unhealthy", "message": str(checks[2])},
    }

    # Determine overall status
    statuses = [result["status"] for result in results.values()]

    if "unhealthy" in statuses:
        overall_status = "unhealthy"
    elif "degraded" in statuses:
        overall_status = "degraded"
    elif "warning" in statuses:
        overall_status = "warning"
    else:
        overall_status = "healthy"

    # Build the response
    response = {
        "status": overall_status,
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT,
        "timestamp": time.time(),
    }

    # Include details if requested
    if include_details:
        response["checks"] = results

    return response


async def require_healthy_service(service: str, settings: Settings = Depends(get_settings)) -> None:
    """
    Require that a service is healthy.

    Args:
        service: The service to check
        settings: Application settings

    Raises:
        ServiceUnavailableError: If the service is unhealthy
    """
    # Map service names to health check functions
    health_checks = {
        "redis": check_redis_connection,
        "external_apis": check_external_apis,
        "system": check_system_resources,
    }

    # Check if the service is valid
    if service not in health_checks:
        raise ValueError(f"Invalid service: {service}")

    # Run the health check
    try:
        result = await health_checks[service]()

        # Check the status
        if result["status"] in ["unhealthy", "degraded"]:
            raise ServiceUnavailableError(
                detail=f"Service {service} is unavailable: {result['message']}", error_type=f"{service}_unavailable"
            )
    except Exception as e:
        if isinstance(e, ServiceUnavailableError):
            raise

        logger.exception(f"Health check failed for {service}")
        raise ServiceUnavailableError(
            detail=f"Service {service} is unavailable: {e!s}", error_type=f"{service}_unavailable"
        )
