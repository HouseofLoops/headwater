import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as dt_time
from decimal import Decimal
from enum import Enum
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest
from pydantic import BaseModel

from app.core.cache_manager import (
    CacheManager,
    CacheSerializationError,
    cached,
    clear_cache,
    delete_from_cache,
    generate_cache_key,
    get_cached_or_fetch,
    get_from_cache,
    set_in_cache,
    start_cleanup_task,
)


class TestCacheManager:
    """Test cases for CacheManager class."""

    def test_init_with_settings(self):
        """Test CacheManager initialization with settings."""
        mock_settings = MagicMock()
        mock_settings.ENABLE_CACHE = True
        mock_settings.CACHE_TTL = 600
        mock_settings.REDIS_URL = None

        manager = CacheManager(settings=mock_settings)

        assert manager.enabled is True
        assert manager.ttl == 600
        assert manager.settings is mock_settings

        # CacheManager no longer owns a Redis connection: Redis access goes
        # through the shared app.core.redis_manager.RedisManager singleton, so
        # the per-instance redis_url / redis_client attributes this test used
        # to assert on no longer exist.
        assert not hasattr(manager, "redis_url")
        assert not hasattr(manager, "redis_client")

    def test_init_defaults(self):
        """Test CacheManager initialization with defaults."""
        with patch('app.core.cache_manager.get_settings') as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.ENABLE_CACHE = True
            mock_settings.CACHE_TTL = 3600
            mock_settings.REDIS_URL = None
            mock_get_settings.return_value = mock_settings

            manager = CacheManager()

            assert manager.enabled is True
            assert manager.ttl == 3600

    def test_serialize_deserialize(self):
        """Test serialization and deserialization."""
        manager = CacheManager()

        # Test dict serialization
        data = {"key": "value", "number": 42}
        serialized = manager._serialize(data)
        deserialized = manager._deserialize(serialized)
        assert deserialized == data

        # Test string serialization (fallback)
        text = "simple string"
        serialized = manager._serialize(text)
        deserialized = manager._deserialize(serialized)
        assert deserialized == text

    def test_generate_key(self):
        """Test cache key generation."""
        manager = CacheManager()

        # Test with namespace
        key = manager._generate_key("test_key", "namespace")
        assert key == "cache:namespace:test_key"

        # Test without namespace
        key = manager._generate_key("test_key")
        assert key == "cache:test_key"

    @pytest.mark.asyncio
    async def test_get_set_memory_only(self):
        """Test get/set operations with in-memory cache only."""
        from unittest.mock import MagicMock
        mock_settings = MagicMock()
        mock_settings.ENABLE_CACHE = True
        mock_settings.CACHE_TTL = 600
        mock_settings.REDIS_URL = None
        manager = CacheManager(settings=mock_settings)

        # Test set
        result = await manager.set("test_key", "test_value", ttl=60)
        assert result is True

        # Test get
        value = await manager.get("test_key")
        assert value == "test_value"

        # Test get with default
        value = await manager.get("nonexistent_key", default="default")
        assert value == "default"

    @pytest.mark.asyncio
    async def test_delete(self):
        """Test delete operation."""
        from unittest.mock import MagicMock
        mock_settings = MagicMock()
        mock_settings.ENABLE_CACHE = True
        mock_settings.CACHE_TTL = 600
        mock_settings.REDIS_URL = None
        manager = CacheManager(settings=mock_settings)

        # Set a value
        await manager.set("test_key", "test_value")

        # Delete it
        result = await manager.delete("test_key")
        assert result is True

        # Verify it's gone
        value = await manager.get("test_key")
        assert value is None

    @pytest.mark.asyncio
    async def test_clear(self):
        """Test clear operation."""
        from unittest.mock import MagicMock
        mock_settings = MagicMock()
        mock_settings.ENABLE_CACHE = True
        mock_settings.CACHE_TTL = 600
        mock_settings.REDIS_URL = None
        manager = CacheManager(settings=mock_settings)

        # Set some values
        await manager.set("key1", "value1")
        await manager.set("key2", "value2", namespace="test")

        # Clear all
        result = await manager.clear()
        assert result is True

        # Verify they're gone
        assert await manager.get("key1") is None
        assert await manager.get("key2", namespace="test") is None

    @pytest.mark.asyncio
    async def test_clear_namespace(self):
        """Test clear operation with namespace."""
        from unittest.mock import MagicMock
        mock_settings = MagicMock()
        mock_settings.ENABLE_CACHE = True
        mock_settings.CACHE_TTL = 600
        mock_settings.REDIS_URL = None
        manager = CacheManager(settings=mock_settings)

        # Set values in different namespaces
        await manager.set("key1", "value1")
        await manager.set("key2", "value2", namespace="test")
        await manager.set("key3", "value3", namespace="test")

        # Clear specific namespace
        result = await manager.clear(namespace="test")
        assert result is True

        # Verify namespace is cleared but other keys remain
        assert await manager.get("key1") == "value1"
        assert await manager.get("key2", namespace="test") is None
        assert await manager.get("key3", namespace="test") is None

    def test_cached_decorator(self):
        """Test cached decorator."""
        from unittest.mock import MagicMock

        from app.core.cache_manager import CacheManager

        # Create a cache manager with caching enabled
        mock_settings = MagicMock()
        mock_settings.ENABLE_CACHE = True
        mock_settings.CACHE_TTL = 600
        mock_settings.REDIS_URL = None
        manager = CacheManager(settings=mock_settings)

        # Clear any existing cache first
        asyncio.run(manager.clear())

        call_count = 0

        @manager.cached(ttl=60)
        async def test_function(x, y=10):
            nonlocal call_count
            call_count += 1
            return x + y

        # First call should execute function
        result1 = asyncio.run(test_function(5, 15))
        assert result1 == 20
        assert call_count == 1

        # Second call with same args should use cache
        result2 = asyncio.run(test_function(5, 15))
        assert result2 == 20
        assert call_count == 1  # Should not have incremented

        # Call with different args should execute again
        result3 = asyncio.run(test_function(10, 15))
        assert result3 == 25
        assert call_count == 2


class TestGenerateCacheKey:
    """Test cases for generate_cache_key function."""

    def test_basic_key_generation(self):
        """Test basic cache key generation."""
        key = generate_cache_key("test_endpoint", param1="value1", param2="value2")
        assert key == "test_endpoint:param1=value1:param2=value2"

    def test_sorted_parameters(self):
        """Test that parameters are sorted for consistent keys."""
        key1 = generate_cache_key("test", z="last", a="first", m="middle")
        key2 = generate_cache_key("test", a="first", m="middle", z="last")
        assert key1 == key2 == "test:a=first:m=middle:z=last"

    def test_list_parameters(self):
        """Test handling of list parameters."""
        key = generate_cache_key("test", items=["a", "b", "c"])
        assert key == "test:items=a,b,c"

    def test_none_values_excluded(self):
        """Test that None values are excluded from keys."""
        key = generate_cache_key("test", included="yes", excluded=None)
        assert key == "test:included=yes"

    def test_long_key_hashing(self):
        """Test that very long keys are hashed."""
        long_value = "x" * 300
        key = generate_cache_key("test", long_param=long_value)

        # Should be hashed due to length
        import hashlib
        expected_hash = hashlib.md5(f"test:long_param={long_value}".encode()).hexdigest()
        assert key == expected_hash

    def test_special_characters(self):
        """Test handling of special characters in parameters."""
        key = generate_cache_key("test", query="hello world & more")
        assert key == "test:query=hello world & more"


class TestGetCachedOrFetch:
    """Test cases for get_cached_or_fetch function."""

    @pytest.mark.asyncio
    async def test_cache_hit(self):
        """Test that cached value is returned when available."""
        from unittest.mock import MagicMock
        mock_settings = MagicMock()
        mock_settings.ENABLE_CACHE = True
        mock_settings.CACHE_TTL = 600
        mock_settings.REDIS_URL = None
        test_manager = CacheManager(settings=mock_settings)

        # Patch the global cache_manager
        import app.core.cache_manager
        original_manager = app.core.cache_manager.cache_manager
        app.core.cache_manager.cache_manager = test_manager

        try:
            # Set up cache with a value
            await set_in_cache("test_key", "cached_value", ttl=60)

            # Mock fetch function (should not be called)
            fetch_called = False
            async def mock_fetch():
                nonlocal fetch_called
                fetch_called = True
                return "fetched_value"

            result = await get_cached_or_fetch("test_key", mock_fetch)

            assert result == "cached_value"
            assert fetch_called is False
        finally:
            # Restore original manager
            app.core.cache_manager.cache_manager = original_manager

    @pytest.mark.asyncio
    async def test_cache_miss_fetch_success(self):
        """Test fetching and caching when cache miss."""
        from unittest.mock import MagicMock
        mock_settings = MagicMock()
        mock_settings.ENABLE_CACHE = True
        mock_settings.CACHE_TTL = 600
        mock_settings.REDIS_URL = None
        test_manager = CacheManager(settings=mock_settings)

        # Patch the global cache_manager
        import app.core.cache_manager
        original_manager = app.core.cache_manager.cache_manager
        app.core.cache_manager.cache_manager = test_manager

        try:
            # Ensure cache is empty
            await delete_from_cache("test_key")

            fetch_called = False
            async def mock_fetch():
                nonlocal fetch_called
                fetch_called = True
                return "fetched_value"

            result = await get_cached_or_fetch("test_key", mock_fetch)

            assert result == "fetched_value"
            assert fetch_called is True

            # Verify it was cached
            cached_value = await get_from_cache("test_key")
            assert cached_value == "fetched_value"
        finally:
            # Restore original manager
            app.core.cache_manager.cache_manager = original_manager

    @pytest.mark.asyncio
    async def test_fetch_failure(self):
        """Test handling of fetch function failure."""
        await delete_from_cache("test_key")

        async def failing_fetch():
            raise ValueError("Fetch failed")

        with pytest.raises(ValueError, match="Fetch failed"):
            await get_cached_or_fetch("test_key", failing_fetch)

    @pytest.mark.asyncio
    async def test_custom_ttl(self):
        """Test custom TTL parameter."""
        from unittest.mock import MagicMock
        mock_settings = MagicMock()
        mock_settings.ENABLE_CACHE = True
        mock_settings.CACHE_TTL = 600
        mock_settings.REDIS_URL = None
        test_manager = CacheManager(settings=mock_settings)

        # Patch the global cache_manager
        import app.core.cache_manager
        original_manager = app.core.cache_manager.cache_manager
        app.core.cache_manager.cache_manager = test_manager

        try:
            await delete_from_cache("test_key")

            async def mock_fetch():
                return "test_value"

            result = await get_cached_or_fetch("test_key", mock_fetch, ttl=120)

            assert result == "test_value"

            # Verify TTL was respected (this is hard to test precisely without timing,
            # but we can at least verify the value is cached)
            cached_value = await get_from_cache("test_key")
            assert cached_value == "test_value"
        finally:
            # Restore original manager
            app.core.cache_manager.cache_manager = original_manager


class TestConvenienceFunctions:
    """Test cases for convenience functions."""

    @pytest.mark.asyncio
    async def test_get_from_cache(self):
        """Test get_from_cache convenience function."""
        from unittest.mock import MagicMock
        mock_settings = MagicMock()
        mock_settings.ENABLE_CACHE = True
        mock_settings.CACHE_TTL = 600
        mock_settings.REDIS_URL = None
        test_manager = CacheManager(settings=mock_settings)

        # Patch the global cache_manager
        import app.core.cache_manager
        original_manager = app.core.cache_manager.cache_manager
        app.core.cache_manager.cache_manager = test_manager

        try:
            await set_in_cache("test_key", "test_value")

            result = await get_from_cache("test_key")
            assert result == "test_value"

            result = await get_from_cache("nonexistent", default="default")
            assert result == "default"
        finally:
            # Restore original manager
            app.core.cache_manager.cache_manager = original_manager

    @pytest.mark.asyncio
    async def test_set_in_cache(self):
        """Test set_in_cache convenience function."""
        from unittest.mock import MagicMock
        mock_settings = MagicMock()
        mock_settings.ENABLE_CACHE = True
        mock_settings.CACHE_TTL = 600
        mock_settings.REDIS_URL = None
        test_manager = CacheManager(settings=mock_settings)

        # Patch the global cache_manager
        import app.core.cache_manager
        original_manager = app.core.cache_manager.cache_manager
        app.core.cache_manager.cache_manager = test_manager

        try:
            result = await set_in_cache("test_key", "test_value", ttl=60)
            assert result is True

            cached_value = await get_from_cache("test_key")
            assert cached_value == "test_value"
        finally:
            # Restore original manager
            app.core.cache_manager.cache_manager = original_manager

    @pytest.mark.asyncio
    async def test_delete_from_cache(self):
        """Test delete_from_cache convenience function."""
        from unittest.mock import MagicMock
        mock_settings = MagicMock()
        mock_settings.ENABLE_CACHE = True
        mock_settings.CACHE_TTL = 600
        mock_settings.REDIS_URL = None
        test_manager = CacheManager(settings=mock_settings)

        # Patch the global cache_manager
        import app.core.cache_manager
        original_manager = app.core.cache_manager.cache_manager
        app.core.cache_manager.cache_manager = test_manager

        try:
            await set_in_cache("test_key", "test_value")

            result = await delete_from_cache("test_key")
            assert result is True

            cached_value = await get_from_cache("test_key")
            assert cached_value is None
        finally:
            # Restore original manager
            app.core.cache_manager.cache_manager = original_manager

    @pytest.mark.asyncio
    async def test_clear_cache_convenience(self):
        """Test clear_cache convenience function."""
        from unittest.mock import MagicMock
        mock_settings = MagicMock()
        mock_settings.ENABLE_CACHE = True
        mock_settings.CACHE_TTL = 600
        mock_settings.REDIS_URL = None
        test_manager = CacheManager(settings=mock_settings)

        # Patch the global cache_manager
        import app.core.cache_manager
        original_manager = app.core.cache_manager.cache_manager
        app.core.cache_manager.cache_manager = test_manager

        try:
            await set_in_cache("key1", "value1")
            await set_in_cache("key2", "value2", namespace="test")

            result = await clear_cache()
            assert result is True

            assert await get_from_cache("key1") is None
            assert await get_from_cache("key2", namespace="test") is None
        finally:
            # Restore original manager
            app.core.cache_manager.cache_manager = original_manager

    def test_cached_decorator_convenience(self):
        """Test cached decorator convenience function."""
        from unittest.mock import MagicMock

        from app.core.cache_manager import CacheManager

        # Create a cache manager with caching enabled and patch the global one
        mock_settings = MagicMock()
        mock_settings.ENABLE_CACHE = True
        mock_settings.CACHE_TTL = 600
        mock_settings.REDIS_URL = None
        test_manager = CacheManager(settings=mock_settings)

        # Patch the global cache_manager
        import app.core.cache_manager
        original_manager = app.core.cache_manager.cache_manager
        app.core.cache_manager.cache_manager = test_manager

        try:
            # Clear cache
            asyncio.run(test_manager.clear())

            call_count = 0

            @cached(ttl=60)
            async def test_function(x):
                nonlocal call_count
                call_count += 1
                return x * 2

            # First call
            result1 = asyncio.run(test_function(5))
            assert result1 == 10
            assert call_count == 1

            # Second call should use cache
            result2 = asyncio.run(test_function(5))
            assert result2 == 10
            assert call_count == 1  # Should not increment

            # Different arg should execute again
            result3 = asyncio.run(test_function(10))
            assert result3 == 20
            assert call_count == 2

        finally:
            # Restore original manager
            app.core.cache_manager.cache_manager = original_manager


class TestCleanupTask:
    """Test cases for cleanup task functionality."""

    @pytest.mark.asyncio
    async def test_cleanup_task_creation(self):
        """Test that cleanup task can be started."""
        # This is mainly to ensure the function doesn't crash
        # The actual cleanup is hard to test without timing controls
        try:
            start_cleanup_task()
            # If we get here without exception, the test passes
            # The function completed successfully
        except Exception as e:
            pytest.fail(f"start_cleanup_task() raised an exception: {e}")

class TestSerializationRoundTrip:
    """
    Round-trip tests for the cache serializer.

    _serialize used to fall back to str(value) for anything JSON could not
    encode. A pydantic model was therefore cached as its repr and handed back
    to the next reader as a corrupted string -- a cache that returns wrong
    data is worse than no cache at all. Values now either round-trip or are
    refused loudly.
    """

    @pytest.fixture
    def manager(self):
        mock_settings = MagicMock()
        mock_settings.ENABLE_CACHE = True
        mock_settings.CACHE_TTL = 600
        mock_settings.REDIS_URL = None
        return CacheManager(settings=mock_settings)

    @pytest.mark.parametrize(
        "value",
        [
            {"key": "value", "number": 42},
            ["a", "b", 3],
            "simple string",
            42,
            3.5,
            True,
            None,
            {"nested": {"list": [1, 2, {"deep": "value"}]}},
        ],
        ids=["dict", "list", "str", "int", "float", "bool", "none", "nested"],
    )
    def test_json_native_types_round_trip(self, manager, value):
        assert manager._deserialize(manager._serialize(value)) == value

    def test_datetime_round_trips_as_datetime(self, manager):
        value = datetime(2024, 3, 17, 12, 30, 45, 123456)

        result = manager._deserialize(manager._serialize(value))

        assert result == value
        assert isinstance(result, datetime), (
            f"datetime came back as {type(result).__name__}: {result!r}"
        )

    def test_datetime_with_timezone_round_trips(self, manager):
        value = datetime(2024, 3, 17, 12, 30, tzinfo=UTC)

        result = manager._deserialize(manager._serialize(value))

        assert result == value

    def test_date_round_trips(self, manager):
        value = date(2024, 3, 17)

        result = manager._deserialize(manager._serialize(value))

        assert result == value
        assert isinstance(result, date)

    def test_time_round_trips(self, manager):
        value = dt_time(23, 59, 58)

        result = manager._deserialize(manager._serialize(value))

        assert result == value
        assert isinstance(result, dt_time)

    def test_timedelta_round_trips(self, manager):
        value = timedelta(days=2, seconds=45)

        assert manager._deserialize(manager._serialize(value)) == value

    def test_decimal_round_trips_without_precision_loss(self, manager):
        value = Decimal("0.1234567890123456789")

        result = manager._deserialize(manager._serialize(value))

        assert result == value
        assert isinstance(result, Decimal)

    def test_uuid_round_trips(self, manager):
        value = UUID("12345678-1234-5678-1234-567812345678")

        result = manager._deserialize(manager._serialize(value))

        assert result == value
        assert isinstance(result, UUID)

    def test_set_round_trips(self, manager):
        value = {"a", "b", "c"}

        result = manager._deserialize(manager._serialize(value))

        assert result == value
        assert isinstance(result, set)

    def test_bytes_round_trip(self, manager):
        value = b"payload bytes"

        assert manager._deserialize(manager._serialize(value)) == value

    def test_pydantic_model_round_trips_as_its_fields(self, manager):
        """
        A model comes back as its field data, not as "id=1 name='x'".

        The class is deliberately not reconstructed: doing so would mean
        importing a dotted path named inside cache content, which is a
        code-execution vector if the cache backend is ever tampered with.
        """
        class Sample(BaseModel):
            id: int
            name: str
            created: datetime

        value = Sample(id=1, name="widget", created=datetime(2024, 1, 2, 3, 4, 5))

        result = manager._deserialize(manager._serialize(value))

        assert isinstance(result, dict)
        assert result["id"] == 1
        assert result["name"] == "widget"
        assert result["created"] == "2024-01-02T03:04:05"
        assert "Sample(" not in str(result)

    def test_nested_pydantic_and_datetime_round_trip(self, manager):
        class Item(BaseModel):
            name: str

        value = {"items": [Item(name="a"), Item(name="b")], "at": date(2024, 5, 6)}

        result = manager._deserialize(manager._serialize(value))

        assert result["items"] == [{"name": "a"}, {"name": "b"}]
        assert result["at"] == date(2024, 5, 6)

    def test_enum_round_trips_as_its_value(self, manager):
        class Colour(Enum):
            RED = "red"

        assert manager._deserialize(manager._serialize(Colour.RED)) == "red"

    def test_dataclass_round_trips_as_dict(self, manager):
        @dataclass
        class Point:
            x: int
            y: int

        assert manager._deserialize(manager._serialize(Point(1, 2))) == {"x": 1, "y": 2}

    def test_unserializable_value_is_refused_not_stringified(self, manager):
        """The old code returned str(value) here and cached garbage."""
        class Opaque:
            def __repr__(self):
                return "<Opaque object at 0xdeadbeef>"

        with pytest.raises(CacheSerializationError) as excinfo:
            manager._serialize(Opaque())

        assert "Opaque" in str(excinfo.value)


class TestSetRefusesUncacheableValues:
    """The refusal must be visible to the caller and to the logs."""

    @pytest.fixture
    def manager(self):
        mock_settings = MagicMock()
        mock_settings.ENABLE_CACHE = True
        mock_settings.CACHE_TTL = 600
        mock_settings.REDIS_URL = None
        return CacheManager(settings=mock_settings)

    @pytest.mark.asyncio
    async def test_set_returns_false_and_logs_for_uncacheable_value(self, manager, caplog):
        class Opaque:
            pass

        with caplog.at_level(logging.ERROR, logger="app.core.cache_manager"):
            result = await manager.set("opaque_key", Opaque(), ttl=60)

        assert result is False, "set() silently claimed success for an uncacheable value"
        assert any("Refusing to cache" in record.getMessage() for record in caplog.records)

    @pytest.mark.asyncio
    async def test_refused_value_is_not_stored(self, manager):
        class Opaque:
            pass

        await manager.set("refused_key", Opaque(), ttl=60)

        assert await manager.get("refused_key", default="MISS") == "MISS"

    @pytest.mark.asyncio
    async def test_datetime_survives_set_then_get(self, manager):
        value = datetime(2024, 7, 4, 9, 0, 0)

        assert await manager.set("dt_key", value, ttl=60) is True
        assert await manager.get("dt_key") == value

    @pytest.mark.asyncio
    async def test_pydantic_model_survives_set_then_get(self, manager):
        class Sample(BaseModel):
            id: int
            name: str

        assert await manager.set("model_key", Sample(id=7, name="x"), ttl=60) is True

        cached_value = await manager.get("model_key")
        assert cached_value == {"id": 7, "name": "x"}
        assert not isinstance(cached_value, str), (
            "model was cached as a string - this is the corruption bug"
        )
