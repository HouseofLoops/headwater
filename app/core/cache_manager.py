"""
Caching utilities for the Headwater application.

This module provides caching functionality to improve performance
by storing frequently accessed data in memory or Redis.

Uses the shared async Redis manager for Redis operations.
"""

import asyncio
import contextlib
import functools
import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta
from datetime import time as dt_time
from decimal import Decimal
from enum import Enum
from typing import Any, TypeVar
from uuid import UUID

from app.core.config import Settings, get_settings
from app.core.log_safety import scrub
from app.core.redis_manager import RedisManager

# Configure logger
logger = logging.getLogger(__name__)

# Type variable for generic cache
T = TypeVar("T")

# In-memory cache storage
# Format: {key: (value, expiry_timestamp)}
_cache_store: dict[str, tuple[Any, float]] = {}

# Thread-safe lock for in-memory cache operations
_cache_lock: asyncio.Lock = asyncio.Lock()

# Cleanup task reference to prevent garbage collection
_cleanup_task: asyncio.Task | None = None

# Shared Redis manager instance (initialized lazily)
_redis_manager: RedisManager | None = None


async def _get_redis_manager() -> RedisManager | None:
    """Get the shared Redis manager instance."""
    global _redis_manager
    if _redis_manager is None:
        _redis_manager = await RedisManager.get_instance()
    return _redis_manager


class CacheSerializationError(TypeError):
    """
    Raised when a value cannot be serialized for caching.

    Caching a value that cannot round-trip is worse than not caching it at
    all, because the corrupted value is served to every later reader. When
    this is raised the value is simply not cached and the failure is logged.
    """


# Key used to tag values that JSON cannot represent natively. Deliberately
# obscure so a genuine payload dict is very unlikely to collide with it.
_TYPE_TAG = "__sf_cache_type__"
_VALUE_KEY = "__sf_cache_value__"


def _encode_uncacheable(value: Any) -> Any:
    """
    ``json.dumps`` ``default`` hook: represent non-JSON types losslessly.

    Args:
        value: A value ``json`` does not know how to encode

    Returns:
        Any: A JSON-encodable representation

    Raises:
        CacheSerializationError: If the type is not supported
    """
    # Pydantic models (v2 and v1). Cached as their field data - the class
    # itself is deliberately NOT reconstructed on read, because importing an
    # arbitrary dotted path named in cache content would be a code-execution
    # vector if the cache backend were ever tampered with.
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return model_dump(mode="json")
        except TypeError:
            return model_dump()
    legacy_dict = getattr(value, "dict", None)
    if callable(legacy_dict) and hasattr(value, "__fields__"):
        return legacy_dict()

    if isinstance(value, datetime):
        return {_TYPE_TAG: "datetime", _VALUE_KEY: value.isoformat()}
    if isinstance(value, date):
        return {_TYPE_TAG: "date", _VALUE_KEY: value.isoformat()}
    if isinstance(value, dt_time):
        return {_TYPE_TAG: "time", _VALUE_KEY: value.isoformat()}
    if isinstance(value, timedelta):
        return {_TYPE_TAG: "timedelta", _VALUE_KEY: value.total_seconds()}
    if isinstance(value, Decimal):
        return {_TYPE_TAG: "decimal", _VALUE_KEY: str(value)}
    if isinstance(value, UUID):
        return {_TYPE_TAG: "uuid", _VALUE_KEY: str(value)}
    if isinstance(value, (set, frozenset)):
        return {_TYPE_TAG: "set", _VALUE_KEY: sorted(value, key=repr)}
    if isinstance(value, bytes):
        return {_TYPE_TAG: "bytes", _VALUE_KEY: value.decode("utf-8", errors="strict")}
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)

    raise CacheSerializationError(
        f"Value of type {type(value).__name__} cannot be serialized for caching. "
        "Convert it to a JSON-compatible structure before caching it."
    )


_ENVELOPE_DECODERS: dict[str, Callable[[Any], Any]] = {
    "datetime": datetime.fromisoformat,
    "date": date.fromisoformat,
    "time": dt_time.fromisoformat,
    "timedelta": lambda v: timedelta(seconds=v),
    "decimal": Decimal,
    "uuid": UUID,
    "set": set,
    "bytes": lambda v: v.encode("utf-8"),
}


def _decode_envelope(obj: dict[str, Any]) -> Any:
    """``json.loads`` ``object_hook``: rebuild values tagged by the encoder."""
    if len(obj) == 2 and _TYPE_TAG in obj and _VALUE_KEY in obj:
        decoder = _ENVELOPE_DECODERS.get(obj[_TYPE_TAG])
        if decoder is not None:
            try:
                return decoder(obj[_VALUE_KEY])
            except TypeError, ValueError:
                logger.warning("Could not decode cached %s value", obj[_TYPE_TAG])
    return obj


class CacheManager:
    """
    Cache manager for storing and retrieving data.

    This class provides a simple interface for caching data in memory
    or Redis, with automatic expiration. Uses async Redis operations
    for better performance.
    """

    def __init__(self, settings: Settings | None = None):
        """
        Initialize the cache manager.

        Args:
            settings: Optional settings instance
        """
        self.settings = settings or get_settings()
        self.enabled = self.settings.ENABLE_CACHE if hasattr(self.settings, "ENABLE_CACHE") else True
        self.ttl = self.settings.CACHE_TTL if hasattr(self.settings, "CACHE_TTL") else 3600  # seconds

    def _serialize(self, value: Any) -> str:
        """
        Serialize a value to a JSON string that round-trips.

        Values that JSON cannot represent natively (datetimes, ``Decimal``,
        ``UUID``, sets, pydantic models, ...) are wrapped in a small typed
        envelope so that :meth:`_deserialize` can rebuild the original object.

        Anything that still cannot be represented is *refused* rather than
        silently degraded. The previous implementation fell back to
        ``str(value)``, which meant a pydantic model or a datetime was cached
        as its ``repr`` and handed back to the next caller as a corrupted
        string.

        Args:
            value: The value to serialize

        Returns:
            str: Serialized value

        Raises:
            CacheSerializationError: If the value cannot be represented
        """
        try:
            return json.dumps(value, default=_encode_uncacheable)
        except CacheSerializationError:
            raise
        except (TypeError, ValueError) as exc:
            raise CacheSerializationError(f"Value of type {type(value).__name__} cannot be cached: {exc}") from exc

    def _deserialize(self, value: str) -> Any:
        """
        Deserialize a string produced by :meth:`_serialize`.

        Args:
            value: The string to deserialize

        Returns:
            Any: Deserialized value
        """
        try:
            return json.loads(value, object_hook=_decode_envelope)
        except json.JSONDecodeError, TypeError:
            # Not JSON at all (e.g. a raw string written by another writer):
            # return it untouched rather than raising.
            return value

    def _generate_key(self, key: str, namespace: str | None = None) -> str:
        """
        Generate a cache key with optional namespace.

        Args:
            key: The base key
            namespace: Optional namespace

        Returns:
            str: The full cache key
        """
        if namespace:
            return f"cache:{namespace}:{key}"
        return f"cache:{key}"

    async def get(self, key: str, namespace: str | None = None, default: Any = None) -> Any:
        """
        Get a value from the cache.

        Args:
            key: The cache key
            namespace: Optional namespace
            default: Default value if key not found

        Returns:
            Any: The cached value or default
        """
        if not self.enabled:
            return default

        full_key = self._generate_key(key, namespace)

        # Try Redis first if available (async)
        redis_manager = await _get_redis_manager()
        if redis_manager and redis_manager.is_available:
            try:
                value = await redis_manager.get(full_key)
                if value is not None:
                    logger.debug(f"Cache hit (Redis): {full_key}")
                    return self._deserialize(value)
            except Exception as e:
                logger.error(f"Redis error in get: {e!s}")

        # Fall back to in-memory cache with thread-safe access
        async with _cache_lock:
            if full_key in _cache_store:
                value, expiry = _cache_store[full_key]

                # Check if expired
                if expiry > time.time():
                    logger.debug(f"Cache hit (memory): {full_key}")
                    return value

                # Remove expired entry
                del _cache_store[full_key]

        logger.debug(f"Cache miss: {full_key}")
        return default

    async def set(self, key: str, value: Any, ttl: int | None = None, namespace: str | None = None) -> bool:
        """
        Set a value in the cache.

        Args:
            key: The cache key
            value: The value to cache
            ttl: Time to live in seconds (None for default)
            namespace: Optional namespace

        Returns:
            bool: True if successful, False otherwise
        """
        if not self.enabled:
            return False

        ttl = ttl if ttl is not None else self.ttl
        full_key = self._generate_key(key, namespace)

        # Serialize up front so that a value which cannot round-trip is
        # refused loudly and consistently, whichever backend is in use.
        # Storing something Redis would mangle only when Redis happens to be
        # down is a correctness trap, not a fallback.
        try:
            serialized = self._serialize(value)
        except CacheSerializationError as exc:
            logger.error("Refusing to cache key %s: %s", scrub(full_key), scrub(exc))
            return False

        # Try Redis first if available (async)
        redis_manager = await _get_redis_manager()
        if redis_manager and redis_manager.is_available:
            try:
                success = await redis_manager.set(full_key, serialized, ttl)
                if success:
                    logger.debug(f"Cache set (Redis): {full_key}, TTL: {ttl}s")
                    return True
            except Exception as e:
                logger.error(f"Redis error in set: {e!s}")

        # Fall back to in-memory cache with thread-safe access.
        # Store the round-tripped value, not the original object, so that a
        # reader sees exactly the same thing whether the hit came from Redis
        # or from memory (and so mutating the caller's object cannot mutate
        # the cached entry).
        async with _cache_lock:
            expiry = time.time() + ttl
            _cache_store[full_key] = (self._deserialize(serialized), expiry)
            logger.debug(f"Cache set (memory): {full_key}, TTL: {ttl}s")
        return True

    async def delete(self, key: str, namespace: str | None = None) -> bool:
        """
        Delete a value from the cache.

        Args:
            key: The cache key
            namespace: Optional namespace

        Returns:
            bool: True if successful, False otherwise
        """
        if not self.enabled:
            return False

        full_key = self._generate_key(key, namespace)
        deleted = False

        # Try Redis first if available (async)
        redis_manager = await _get_redis_manager()
        if redis_manager and redis_manager.is_available:
            try:
                count = await redis_manager.delete(full_key)
                if count > 0:
                    logger.debug(f"Cache delete (Redis): {full_key}")
                    deleted = True
            except Exception as e:
                logger.error(f"Redis error in delete: {e!s}")

        # Also delete from in-memory cache with thread-safe access
        async with _cache_lock:
            if full_key in _cache_store:
                del _cache_store[full_key]
                logger.debug(f"Cache delete (memory): {full_key}")
                deleted = True

        return deleted

    async def clear(self, namespace: str | None = None) -> bool:
        """
        Clear all values from the cache or a specific namespace.

        Args:
            namespace: Optional namespace to clear

        Returns:
            bool: True if successful, False otherwise
        """
        if not self.enabled:
            return False

        # Clear Redis cache if available (async)
        redis_manager = await _get_redis_manager()
        if redis_manager and redis_manager.is_available:
            try:
                pattern = f"cache:{namespace}:*" if namespace else "cache:*"

                keys = await redis_manager.keys(pattern)
                if keys:
                    await redis_manager.delete(*keys)
                    logger.debug(f"Cache clear (Redis): {len(keys)} keys")
            except Exception as e:
                logger.error(f"Redis error in clear: {e!s}")

        # Clear in-memory cache with thread-safe access
        async with _cache_lock:
            if namespace:
                prefix = f"cache:{namespace}:"
                keys_to_delete = [k for k in _cache_store if k.startswith(prefix)]
                for k in keys_to_delete:
                    del _cache_store[k]
                logger.debug(f"Cache clear (memory) for namespace: {namespace}, {len(keys_to_delete)} keys")
            else:
                count = len(_cache_store)
                _cache_store.clear()
                logger.debug(f"Cache clear (memory): {count} keys")

        return True

    def cached(
        self, ttl: int | None = None, namespace: str | None = None, key_builder: Callable[..., str] | None = None
    ):
        """
        Decorator for caching function results.

        Args:
            ttl: Time to live in seconds (None for default)
            namespace: Optional namespace
            key_builder: Optional function to build the cache key

        Returns:
            Callable: Decorated function
        """

        def decorator(func):
            @functools.wraps(func)
            async def wrapper(*args, **kwargs):
                if not self.enabled:
                    return await func(*args, **kwargs)

                # Build cache key
                if key_builder:
                    key = key_builder(*args, **kwargs)
                else:
                    # Default key builder: function name + args + kwargs
                    key_parts = [func.__name__]

                    # Add args to key
                    for arg in args:
                        key_parts.append(str(arg))

                    # Add kwargs to key (sorted for consistency)
                    for k, v in sorted(kwargs.items()):
                        key_parts.append(f"{k}={v}")

                    # Join and hash if too long
                    key_str = ":".join(key_parts)
                    # Redis keys are limited to 512 bytes
                    key = (
                        hashlib.md5(key_str.encode(), usedforsecurity=False).hexdigest()
                        if len(key_str) > 250
                        else key_str
                    )

                # Try to get from cache
                cached_value = await self.get(key, namespace)
                if cached_value is not None:
                    return cached_value

                # Call the function
                result = await func(*args, **kwargs)

                # Cache the result
                await self.set(key, result, ttl, namespace)

                return result

            return wrapper

        return decorator


# Cleanup task for the in-memory cache
async def cleanup_cache_store():
    """
    Periodically clean up expired cache entries.

    This task runs in the background to remove expired entries from
    the in-memory cache store. Uses thread-safe access to the cache store.
    """
    while True:
        try:
            now = time.time()

            # Thread-safe access to find and remove expired keys
            async with _cache_lock:
                # Find expired keys
                expired_keys = []
                for key, (_, expiry) in _cache_store.items():
                    if expiry <= now:
                        expired_keys.append(key)

                # Remove expired keys
                for key in expired_keys:
                    del _cache_store[key]

                # Log cleanup
                if expired_keys:
                    logger.debug(f"Cleaned up {len(expired_keys)} expired cache entries")
        except asyncio.CancelledError:
            logger.info("Cache cleanup task cancelled")
            break
        except Exception as e:
            logger.error(f"Error in cache store cleanup: {e!s}")

        # Sleep for a while
        await asyncio.sleep(60)  # Clean up every minute


def start_cleanup_task():
    """
    Start the cache store cleanup task.

    Stores the task reference to prevent garbage collection and allow
    graceful shutdown.
    """
    global _cleanup_task
    if _cleanup_task is None or _cleanup_task.done():
        _cleanup_task = asyncio.create_task(cleanup_cache_store())
        logger.debug("Cache cleanup task started")


async def stop_cleanup_task():
    """
    Stop the cache store cleanup task gracefully.

    Cancels the running cleanup task and waits for it to complete.
    """
    global _cleanup_task
    if _cleanup_task is not None and not _cleanup_task.done():
        _cleanup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _cleanup_task
        logger.info("Cache cleanup task stopped")
    _cleanup_task = None


# Create a global cache manager instance
cache_manager = CacheManager()


# Convenience functions
async def get_from_cache(key: str, namespace: str | None = None, default: Any = None) -> Any:
    """
    Get a value from the cache.

    Args:
        key: The cache key
        namespace: Optional namespace
        default: Default value if key not found

    Returns:
        Any: The cached value or default
    """
    return await cache_manager.get(key, namespace, default)


async def set_in_cache(key: str, value: Any, ttl: int | None = None, namespace: str | None = None) -> bool:
    """
    Set a value in the cache.

    Args:
        key: The cache key
        value: The value to cache
        ttl: Time to live in seconds (None for default)
        namespace: Optional namespace

    Returns:
        bool: True if successful, False otherwise
    """
    return await cache_manager.set(key, value, ttl, namespace)


async def delete_from_cache(key: str, namespace: str | None = None) -> bool:
    """
    Delete a value from the cache.

    Args:
        key: The cache key
        namespace: Optional namespace

    Returns:
        bool: True if successful, False otherwise
    """
    return await cache_manager.delete(key, namespace)


async def clear_cache(namespace: str | None = None) -> bool:
    """
    Clear all values from the cache or a specific namespace.

    Args:
        namespace: Optional namespace to clear

    Returns:
        bool: True if successful, False otherwise
    """
    return await cache_manager.clear(namespace)


def cached(ttl: int | None = None, namespace: str | None = None, key_builder: Callable[..., str] | None = None):
    """
    Decorator for caching function results.

    Args:
        ttl: Time to live in seconds (None for default)
        namespace: Optional namespace
        key_builder: Optional function to build the cache key

    Returns:
        Callable: Decorated function
    """
    return cache_manager.cached(ttl, namespace, key_builder)


def generate_cache_key(base_key: str, **kwargs) -> str:
    """
    Generate a consistent cache key from a base key and parameters.

    Args:
        base_key: The base cache key (e.g., "trends_interest_over_time")
        **kwargs: Additional parameters to include in the key

    Returns:
        str: A consistent cache key
    """
    # Sort kwargs for consistent key generation
    sorted_kwargs = sorted(kwargs.items())

    # Build key components
    key_parts = [base_key]

    for key, value in sorted_kwargs:
        if value is not None:
            # Convert value to string and handle special cases
            value_str = ",".join(str(v) for v in value) if isinstance(value, list) else str(value)
            key_parts.append(f"{key}={value_str}")

    # Join parts with colons
    full_key = ":".join(key_parts)

    # If key is too long, hash it
    if len(full_key) > 250:  # Redis key limit is 512 bytes, be conservative
        full_key = hashlib.md5(full_key.encode(), usedforsecurity=False).hexdigest()

    return full_key


async def get_cached_or_fetch(cache_key: str, fetch_func: Callable[[], Any], ttl: int | None = None) -> Any:
    """
    Get data from cache or fetch and cache it if not found.

    Args:
        cache_key: The cache key to use
        fetch_func: Async function to call if data not in cache
        ttl: Optional TTL override

    Returns:
        Any: The cached or freshly fetched data
    """
    # Try to get from cache first
    cached_data = await cache_manager.get(cache_key)
    if cached_data is not None:
        logger.debug(f"Cache hit for key: {cache_key}")
        return cached_data

    # Cache miss - fetch the data
    logger.debug(f"Cache miss for key: {cache_key}, fetching data")
    try:
        data = await fetch_func()

        # Cache the result
        await cache_manager.set(cache_key, data, ttl)
        logger.debug(f"Cached data for key: {cache_key}")

        return data
    except Exception as e:
        logger.error(f"Error fetching data for cache key {cache_key}: {e}")
        raise
