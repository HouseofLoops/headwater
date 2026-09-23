"""
Tests for app.core.http_client's global manager lifecycle.

The manager owns the shared httpx connection pools. get_http_client_manager()
used to build a brand new manager (and therefore a brand new pool) on every
call without ever assigning the module global, so shutdown closed exactly one
of the many pools that had been created and the rest leaked for the life of
the process.
"""

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
