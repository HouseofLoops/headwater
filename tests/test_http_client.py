"""
Tests for app.core.http_client's global manager lifecycle.

The manager owns the shared httpx connection pools. get_http_client_manager()
used to build a brand new manager (and therefore a brand new pool) on every
call without ever assigning the module global, so shutdown closed exactly one
of the many pools that had been created and the rest leaked for the life of
the process.
"""

import asyncio

import httpx
import pytest

from app.core import http_client as http_client_module
from app.core.http_client import (
    HTTPClientManager,
    get_http_client_manager,
    set_http_client_manager,
    shutdown_http_client_manager,
)


@pytest.fixture(autouse=True)
def reset_global_manager():
    """Keep the module global isolated between tests."""
    original = http_client_module._http_client_manager
    set_http_client_manager(None)
    yield
    set_http_client_manager(original)


class TestGlobalManagerIsASingleton:
    def test_two_calls_return_the_same_instance(self):
        first = get_http_client_manager()
        second = get_http_client_manager()

        assert first is second, (
            "get_http_client_manager() built a new manager (and a new "
            "connection pool) instead of reusing the global one"
        )

    def test_first_call_assigns_the_module_global(self):
        assert http_client_module._http_client_manager is None

        manager = get_http_client_manager()

        assert http_client_module._http_client_manager is manager

    def test_injected_manager_is_returned(self):
        injected = HTTPClientManager()
        set_http_client_manager(injected)

        assert get_http_client_manager() is injected


class TestShutdown:
    @pytest.mark.asyncio
    async def test_shutdown_closes_the_shared_manager(self):
        manager = get_http_client_manager()
        client = await manager.get_client()

        assert manager._clients, "expected a pooled client to have been created"
        assert not client.is_closed

        await shutdown_http_client_manager()

        assert client.is_closed, "shutdown did not close the pooled client"
        assert manager._clients == {}
        assert http_client_module._http_client_manager is None

    @pytest.mark.asyncio
    async def test_shutdown_closes_the_pool_every_caller_shared(self):
        """
        The leak, expressed as a test: a client created through one call site
        must be closed by shutdown, no matter which call site created it.
        """
        client_a = await get_http_client_manager().get_client()
        client_b = await get_http_client_manager().get_client()

        assert client_a is client_b

        await shutdown_http_client_manager()

        assert client_a.is_closed
        assert client_b.is_closed

    @pytest.mark.asyncio
    async def test_shutdown_is_safe_when_nothing_was_created(self):
        await shutdown_http_client_manager()

        assert http_client_module._http_client_manager is None

    @pytest.mark.asyncio
    async def test_lifespan_manager_closes_on_exit(self):
        from app.core.http_client import lifespan_manager

        async with lifespan_manager() as manager:
            client = await manager.get_client()
            assert not client.is_closed

        assert client.is_closed
        assert http_client_module._http_client_manager is None


class TestPoolExhaustionRecovery:
    """A permanently exhausted pool is rebuilt (production, 2026-10-29 .. 2026-11-18)."""

    @pytest.mark.asyncio
    async def test_get_rebuilds_the_pool_once_on_pool_timeout(self):
        manager = HTTPClientManager()
        manager.settings = type("S", (), {"HTTP_READ_TIMEOUT": -5.0})()  # close immediately in the test
        calls = []

        def exhausted(request):
            raise httpx.PoolTimeout("pool exhausted")

        def healthy(request):
            calls.append(str(request.url))
            return httpx.Response(200, text="ok")

        stale = httpx.AsyncClient(transport=httpx.MockTransport(exhausted))
        manager._clients["default"] = stale
        fresh = httpx.AsyncClient(transport=httpx.MockTransport(healthy))
        original_get_client = manager.get_client

        async def get_client(proxy_url=None):
            if "default" not in manager._clients:
                manager._clients["default"] = fresh
            return await original_get_client(proxy_url)

        manager.get_client = get_client
        response = await manager.get("https://example.com/x")

        assert response.status_code == 200
        assert calls == ["https://example.com/x"]
        assert manager._clients["default"] is fresh
        # Closed later, so requests still running on the old pool can finish.
        assert not stale.is_closed
        await asyncio.gather(*manager._closing)
        assert stale.is_closed

    @pytest.mark.asyncio
    async def test_get_does_not_retry_other_errors(self):
        manager = HTTPClientManager()

        def refused(request):
            raise httpx.ConnectError("refused")

        client = httpx.AsyncClient(transport=httpx.MockTransport(refused))
        manager._clients["default"] = client
        with pytest.raises(httpx.ConnectError):
            await manager.get("https://example.com/x")
        assert manager._clients["default"] is client

    @pytest.mark.asyncio
    async def test_reset_keeps_a_client_another_request_already_replaced(self):
        manager = HTTPClientManager()
        stale = httpx.AsyncClient()
        replacement = httpx.AsyncClient()
        manager._clients["default"] = replacement
        await manager.reset_client(None, stale, close_after=0)
        assert manager._clients["default"] is replacement
        await asyncio.gather(*manager._closing)
        assert stale.is_closed
        assert not replacement.is_closed
        await replacement.aclose()
