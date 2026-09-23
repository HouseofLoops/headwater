from app.core.proxy import mask_proxy

"""
HTTP Client Manager with Connection Pooling.

This module provides a centralized HTTP client manager that implements
connection pooling, request batching, and enhanced error handling.
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.constants import DEFAULT_USER_AGENT

logger = logging.getLogger("uvicorn")


class HTTPClientManager:
    """
    HTTP Client Manager with connection pooling and batch processing capabilities.
    """

    def __init__(self):
        self.settings = get_settings()
        self._clients: dict[str, httpx.AsyncClient] = {}
        self._client_lock = asyncio.Lock()
        self._stats = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "connection_pool_hits": 0,
            "connection_pool_misses": 0,
            "average_response_time": 0.0,
        }

    async def get_client(self, proxy_url: str | None = None) -> httpx.AsyncClient:
        """
        Get or create an HTTP client with connection pooling.

        Args:
            proxy_url: Optional proxy URL to use for the client

        Returns:
            httpx.AsyncClient: Configured HTTP client
        """
        client_key = proxy_url or "default"

        async with self._client_lock:
            if client_key not in self._clients:
                # Create new client with connection pooling settings
                limits = httpx.Limits(
                    max_keepalive_connections=self.settings.HTTP_MAX_KEEPALIVE_CONNECTIONS,
                    max_connections=self.settings.HTTP_CONNECTION_POOL_SIZE,
                    keepalive_expiry=30.0,
                )

                timeout = httpx.Timeout(
                    connect=self.settings.HTTP_CONNECTION_TIMEOUT,
                    read=self.settings.HTTP_READ_TIMEOUT,
                    write=10.0,
                    pool=5.0,
                )

                client_config = {
                    "limits": limits,
                    "timeout": timeout,
                    "follow_redirects": True,
                    "headers": {"User-Agent": DEFAULT_USER_AGENT},
                }

                if proxy_url:
                    # httpx 0.28+ uses 'proxy' instead of 'proxies'
                    client_config["proxy"] = proxy_url
                    logger.debug("Created HTTP client with proxy: %s", mask_proxy(proxy_url))
                else:
                    logger.debug("Created HTTP client without proxy")

                self._clients[client_key] = httpx.AsyncClient(**client_config)
                self._stats["connection_pool_misses"] += 1
            else:
                self._stats["connection_pool_hits"] += 1

            return self._clients[client_key]

    async def make_request(
        self,
        url: str,
        method: str = "GET",
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        proxy_url: str | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """
        Make an HTTP request with comprehensive error handling and metadata.

        Args:
            url: Request URL
            method: HTTP method
            params: Query parameters
            headers: Request headers
            proxy_url: Optional proxy URL
            **kwargs: Additional request arguments

        Returns:
            Dict containing response data and metadata
        """
        start_time = time.time()
        client = await self.get_client(proxy_url)

        request_metadata = {
            "url": url,
            "method": method,
            "params": params,
            "headers": headers,
            "proxy_used": proxy_url is not None,
            "timestamp": start_time,
        }

        try:
            self._stats["total_requests"] += 1

            response = await client.request(method=method, url=url, params=params, headers=headers, **kwargs)

            response_time = time.time() - start_time
            self._update_response_time_stats(response_time)

            if response.status_code == 200:
                self._stats["successful_requests"] += 1
            else:
                self._stats["failed_requests"] += 1

            # Try to parse response content
            content = None
            content_type = response.headers.get("content-type", "")

            if "application/json" in content_type:
                try:
                    content = response.json()
                except (ValueError, TypeError) as parse_error:
                    logger.warning("Failed to parse JSON response: %s", str(parse_error))
                    content = response.text
            else:
                content = response.text

            return {
                "success": response.status_code == 200,
                "status_code": response.status_code,
                "content": content,
                "headers": dict(response.headers),
                "response_time": response_time,
                "request_metadata": request_metadata,
                "connection_info": {
                    "pool_size": len(self._clients),
                    "keepalive_connections": getattr(client, "_pool", {}).get("num_connections", 0),
                },
            }

        except (httpx.RequestError, httpx.TimeoutException, httpx.ConnectError) as e:
            response_time = time.time() - start_time
            self._stats["failed_requests"] += 1

            logger.error("HTTP request failed: %s", str(e))
            return {
                "success": False,
                "error": str(e),
                "response_time": response_time,
                "request_metadata": request_metadata,
                "connection_info": {"pool_size": len(self._clients), "keepalive_connections": 0},
            }

    async def batch_requests(
        self, requests: list[dict[str, Any]], max_concurrent: int | None = None, timeout: float | None = None
    ) -> list[dict[str, Any]]:
        """
        Execute multiple HTTP requests in parallel with batch processing.

        Args:
            requests: List of request dictionaries
            max_concurrent: Maximum concurrent requests
            timeout: Optional timeout in seconds (defaults to BATCH_TIMEOUT setting)

        Returns:
            List of response dictionaries
        """
        if not self.settings.BATCH_PROCESSING_ENABLED:
            # Fall back to sequential processing
            results = []
            for req in requests:
                result = await self.make_request(**req)
                results.append(result)
            return results

        max_concurrent = max_concurrent or self.settings.MAX_CONCURRENT_BATCHES
        batch_timeout = timeout or self.settings.BATCH_TIMEOUT
        semaphore = asyncio.Semaphore(max_concurrent)

        async def make_request_with_semaphore(req: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                return await self.make_request(**req)

        # Execute all requests in parallel with timeout protection
        tasks = [make_request_with_semaphore(req) for req in requests]

        try:
            # Wrap gather with timeout to prevent indefinite hanging
            results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=batch_timeout)
        except asyncio.TimeoutError:
            logger.error("Batch request timed out after %.1f seconds with %d requests", batch_timeout, len(requests))
            # Return timeout errors for all requests
            return [
                {
                    "success": False,
                    "error": f"Batch request timed out after {batch_timeout} seconds",
                    "request_metadata": req,
                }
                for req in requests
            ]

        # Handle any exceptions that occurred
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error("Batch request %d failed: %s", i, str(result))
                processed_results.append(
                    {
                        "success": False,
                        "error": str(result),
                        "request_metadata": requests[i] if i < len(requests) else {},
                    }
                )
            else:
                processed_results.append(result)

        return processed_results

    def _update_response_time_stats(self, response_time: float):
        """Update rolling average response time statistics."""
        current_avg = self._stats["average_response_time"]
        total_requests = self._stats["total_requests"]

        if total_requests == 1:
            self._stats["average_response_time"] = response_time
        else:
            # Rolling average calculation
            self._stats["average_response_time"] = (
                (current_avg * (total_requests - 1)) + response_time
            ) / total_requests

    def get_stats(self) -> dict[str, Any]:
        """Get HTTP client statistics."""
        return {
            **self._stats,
            "active_clients": len(self._clients),
            "connection_pool_efficiency": (
                self._stats["connection_pool_hits"]
                / max(1, self._stats["connection_pool_hits"] + self._stats["connection_pool_misses"])
            )
            * 100,
        }

    def get_request_count(self) -> int:
        """Get total number of requests made."""
        return self._stats["total_requests"]

    def get_connection_stats(self) -> dict[str, Any]:
        """Get connection pool statistics."""
        return {
            "active_clients": len(self._clients),
            "pool_hits": self._stats["connection_pool_hits"],
            "pool_misses": self._stats["connection_pool_misses"],
            "pool_efficiency": (
                self._stats["connection_pool_hits"]
                / max(1, self._stats["connection_pool_hits"] + self._stats["connection_pool_misses"])
            )
            * 100,
        }

    async def close_all_clients(self):
        """Close all HTTP clients and cleanup connections."""
        async with self._client_lock:
            for client in self._clients.values():
                await client.aclose()
            self._clients.clear()
            logger.info("Closed all HTTP clients")


# Global HTTP client manager instance
_http_client_manager: HTTPClientManager | None = None


def get_http_client_manager() -> HTTPClientManager:
    """
    Get the global HTTP client manager instance.

    The manager owns the shared httpx connection pools, so exactly one
    instance must exist for the life of the process. Previously this
    function built (and leaked) a brand new manager on every call, which
    meant ``close_all_clients()`` at shutdown only ever closed one of the
    many pools that had been created.
    """
    global _http_client_manager
    if _http_client_manager is None:
        _http_client_manager = HTTPClientManager()
    return _http_client_manager


def set_http_client_manager(manager: HTTPClientManager | None) -> None:
    """Set (or clear, with ``None``) the global HTTP client manager instance."""
    global _http_client_manager
    _http_client_manager = manager


async def shutdown_http_client_manager() -> None:
    """Close the global HTTP client manager and drop the reference."""
    global _http_client_manager
    if _http_client_manager is not None:
        await _http_client_manager.close_all_clients()
        _http_client_manager = None


@asynccontextmanager
async def lifespan_manager():
    """Context manager for application lifespan management."""
    manager = get_http_client_manager()
    try:
        yield manager
    finally:
        await shutdown_http_client_manager()
