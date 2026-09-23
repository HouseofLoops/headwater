"""
Cache backend implementations using the Strategy pattern.

This module provides pluggable cache backends for the CacheManager.
Each backend implements the CacheBackend abstract base class,
allowing easy switching between different caching strategies.

Uses the shared async RedisManager for Redis operations.
"""
import asyncio
import json
import logging
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.core.redis_manager import RedisManager

logger = logging.getLogger(__name__)


class CacheBackend(ABC):
    """
    Abstract base class for cache backends.

    Implementations must provide async methods for all cache operations.
    The strategy pattern allows swapping backends without changing the CacheManager.
    """

    @abstractmethod
    async def get(self, key: str) -> Any | None:
        """
        Get a value from the cache.

        Args:
            key: The cache key

        Returns:
            The cached value or None if not found
        """
        pass

    @abstractmethod
    async def set(self, key: str, value: Any, ttl: int) -> bool:
        """
        Set a value in the cache.

        Args:
            key: The cache key
            value: The value to cache
            ttl: Time to live in seconds

        Returns:
            True if successful, False otherwise
        """
        pass

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """
        Delete a value from the cache.

        Args:
            key: The cache key

        Returns:
            True if key existed and was deleted, False otherwise
        """
        pass

    @abstractmethod
    async def clear(self, pattern: str | None = None) -> int:
        """
        Clear values from the cache.

        Args:
            pattern: Optional pattern to match keys (e.g., "cache:namespace:*")

        Returns:
            Number of keys deleted
        """
        pass

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """
        Check if a key exists in the cache.

        Args:
            key: The cache key

        Returns:
            True if key exists, False otherwise
        """
        pass

    @abstractmethod
    async def get_stats(self) -> dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dictionary with cache statistics
        """
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """
        Check if the backend is healthy.

        Returns:
            True if healthy, False otherwise
        """
        pass


class MemoryCacheBackend(CacheBackend):
    """
    In-memory cache backend using a dictionary.

    Thread-safe implementation using asyncio.Lock for concurrent access.
    Suitable for single-instance deployments or as a fallback.
    """

    def __init__(self):
        """Initialize the memory cache backend."""
        self._store: dict[str, tuple[Any, float]] = {}
        self._lock = asyncio.Lock()
        self._hits = 0
        self._misses = 0
        self._sets = 0
        self._deletes = 0

    async def get(self, key: str) -> Any | None:
        """Get a value from the in-memory cache."""
        async with self._lock:
            if key in self._store:
                value, expiry = self._store[key]

                # Check if expired
                if expiry > time.time():
                    self._hits += 1
                    logger.debug(f"Memory cache hit: {key}")
                    return value

                # Remove expired entry
                del self._store[key]

            self._misses += 1
            logger.debug(f"Memory cache miss: {key}")
            return None

    async def set(self, key: str, value: Any, ttl: int) -> bool:
        """Set a value in the in-memory cache."""
        async with self._lock:
            expiry = time.time() + ttl
            self._store[key] = (value, expiry)
            self._sets += 1
            logger.debug(f"Memory cache set: {key}, TTL: {ttl}s")
            return True

    async def delete(self, key: str) -> bool:
        """Delete a value from the in-memory cache."""
        async with self._lock:
            if key in self._store:
                del self._store[key]
                self._deletes += 1
                logger.debug(f"Memory cache delete: {key}")
                return True
            return False

    async def clear(self, pattern: str | None = None) -> int:
        """Clear values from the in-memory cache."""
        async with self._lock:
            if pattern:
                # Pattern matching (simple prefix matching)
                prefix = pattern.rstrip("*")
                keys_to_delete = [k for k in self._store.keys() if k.startswith(prefix)]
                for key in keys_to_delete:
                    del self._store[key]
                count = len(keys_to_delete)
            else:
                count = len(self._store)
                self._store.clear()

            logger.debug(f"Memory cache clear: {count} keys deleted")
            return count

    async def exists(self, key: str) -> bool:
        """Check if a key exists in the in-memory cache."""
        async with self._lock:
            if key in self._store:
                _, expiry = self._store[key]
                if expiry > time.time():
                    return True
                # Clean up expired entry
                del self._store[key]
            return False

    async def get_stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        async with self._lock:
            total = self._hits + self._misses
            hit_rate = (self._hits / total * 100) if total > 0 else 0.0
            return {
                "backend": "memory",
                "size": len(self._store),
                "hits": self._hits,
                "misses": self._misses,
                "sets": self._sets,
                "deletes": self._deletes,
                "hit_rate": f"{hit_rate:.2f}%"
            }

    async def health_check(self) -> bool:
        """Check if the backend is healthy."""
        return True

    async def cleanup_expired(self) -> int:
        """
        Remove expired entries from the cache.

        Returns:
            Number of entries removed
        """
        async with self._lock:
            now = time.time()
            expired_keys = [
                key for key, (_, expiry) in self._store.items()
                if expiry <= now
            ]
            for key in expired_keys:
                del self._store[key]

            if expired_keys:
                logger.debug(f"Cleaned up {len(expired_keys)} expired cache entries")
            return len(expired_keys)


class RedisCacheBackend(CacheBackend):
    """
    Redis cache backend for distributed caching.

    Uses the shared async RedisManager for all Redis operations.
    Suitable for multi-instance deployments where cache needs to be shared.
    Falls back gracefully if Redis is unavailable.
    """

    def __init__(self, redis_url: str = None):
        """
        Initialize the Redis cache backend.

        Args:
            redis_url: Redis connection URL (unused, kept for compatibility)
                      Uses shared RedisManager instead
        """
        self._manager: RedisManager | None = None
        self._hits = 0
        self._misses = 0
        self._sets = 0
        self._deletes = 0

    async def _get_manager(self) -> RedisManager | None:
        """Get the shared Redis manager."""
        if self._manager is None:
            from app.core.redis_manager import RedisManager
            self._manager = await RedisManager.get_instance()
        return self._manager

    def _serialize(self, value: Any) -> str:
        """Serialize a value to JSON string."""
        try:
            return json.dumps(value)
        except (TypeError, ValueError):
            return str(value)

    def _deserialize(self, value: str) -> Any:
        """Deserialize a JSON string to value."""
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError, AttributeError):
            return value

    async def get(self, key: str) -> Any | None:
        """Get a value from Redis."""
        manager = await self._get_manager()
        if not manager or not manager.is_available:
            return None

        try:
            value = await manager.get(key)
            if value is not None:
                self._hits += 1
                logger.debug(f"Redis cache hit: {key}")
                return self._deserialize(value)
            self._misses += 1
            logger.debug(f"Redis cache miss: {key}")
            return None
        except Exception as e:
            logger.error(f"Redis get error: {e}")
            return None

    async def set(self, key: str, value: Any, ttl: int) -> bool:
        """Set a value in Redis."""
        manager = await self._get_manager()
        if not manager or not manager.is_available:
            return False

        try:
            serialized = self._serialize(value)
            success = await manager.set(key, serialized, ttl)
            if success:
                self._sets += 1
                logger.debug(f"Redis cache set: {key}, TTL: {ttl}s")
            return success
        except Exception as e:
            logger.error(f"Redis set error: {e}")
            return False

    async def delete(self, key: str) -> bool:
        """Delete a value from Redis."""
        manager = await self._get_manager()
        if not manager or not manager.is_available:
            return False

        try:
            count = await manager.delete(key)
            if count > 0:
                self._deletes += 1
                logger.debug(f"Redis cache delete: {key}")
                return True
            return False
        except Exception as e:
            logger.error(f"Redis delete error: {e}")
            return False

    async def clear(self, pattern: str | None = None) -> int:
        """Clear values from Redis."""
        manager = await self._get_manager()
        if not manager or not manager.is_available:
            return 0

        try:
            search_pattern = pattern if pattern else "cache:*"
            keys = await manager.keys(search_pattern)

            if keys:
                count = await manager.delete(*keys)
                logger.debug(f"Redis cache clear: {count} keys deleted")
                return count
            return 0
        except Exception as e:
            logger.error(f"Redis clear error: {e}")
            return 0

    async def exists(self, key: str) -> bool:
        """Check if a key exists in Redis."""
        manager = await self._get_manager()
        if not manager or not manager.is_available:
            return False

        try:
            count = await manager.exists(key)
            return count > 0
        except Exception as e:
            logger.error(f"Redis exists error: {e}")
            return False

    async def get_stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        manager = await self._get_manager()
        total = self._hits + self._misses
        hit_rate = (self._hits / total * 100) if total > 0 else 0.0

        stats = {
            "backend": "redis",
            "connected": manager.is_available if manager else False,
            "hits": self._hits,
            "misses": self._misses,
            "sets": self._sets,
            "deletes": self._deletes,
            "hit_rate": f"{hit_rate:.2f}%"
        }

        if manager and manager.is_available:
            health = await manager.health_check()
            stats["latency_ms"] = health.get("latency_ms")

        return stats

    async def health_check(self) -> bool:
        """Check if Redis is healthy."""
        manager = await self._get_manager()
        if not manager:
            return False

        health = await manager.health_check()
        return health.get("status") == "healthy"


class TieredCacheBackend(CacheBackend):
    """
    Tiered cache backend combining memory and Redis.

    Uses memory cache as L1 (fast) and Redis as L2 (distributed).
    Reads check L1 first, then L2. Writes update both levels.
    """

    def __init__(self, redis_url: str | None = None):
        """
        Initialize the tiered cache backend.

        Args:
            redis_url: Optional Redis URL for L2 cache
        """
        self._memory = MemoryCacheBackend()
        self._redis: RedisCacheBackend | None = None
        if redis_url:
            self._redis = RedisCacheBackend(redis_url)

    async def get(self, key: str) -> Any | None:
        """Get from L1, then L2 if miss."""
        # Check L1 (memory) first
        value = await self._memory.get(key)
        if value is not None:
            return value

        # Check L2 (Redis) if available
        if self._redis:
            value = await self._redis.get(key)
            if value is not None:
                # Promote to L1 with a short TTL
                await self._memory.set(key, value, 60)
                return value

        return None

    async def set(self, key: str, value: Any, ttl: int) -> bool:
        """Set in both L1 and L2."""
        # Set in L1 (memory)
        memory_result = await self._memory.set(key, value, min(ttl, 300))  # Cap memory TTL

        # Set in L2 (Redis) if available
        redis_result = True
        if self._redis:
            redis_result = await self._redis.set(key, value, ttl)

        return memory_result or redis_result

    async def delete(self, key: str) -> bool:
        """Delete from both L1 and L2."""
        memory_result = await self._memory.delete(key)
        redis_result = True
        if self._redis:
            redis_result = await self._redis.delete(key)
        return memory_result or redis_result

    async def clear(self, pattern: str | None = None) -> int:
        """Clear from both L1 and L2."""
        memory_count = await self._memory.clear(pattern)
        redis_count = 0
        if self._redis:
            redis_count = await self._redis.clear(pattern)
        return memory_count + redis_count

    async def exists(self, key: str) -> bool:
        """Check existence in L1, then L2."""
        if await self._memory.exists(key):
            return True
        if self._redis:
            return await self._redis.exists(key)
        return False

    async def get_stats(self) -> dict[str, Any]:
        """Get combined statistics."""
        memory_stats = await self._memory.get_stats()
        stats = {
            "backend": "tiered",
            "l1_memory": memory_stats
        }
        if self._redis:
            stats["l2_redis"] = await self._redis.get_stats()
        return stats

    async def health_check(self) -> bool:
        """Check if at least one tier is healthy."""
        memory_healthy = await self._memory.health_check()
        redis_healthy = True
        if self._redis:
            redis_healthy = await self._redis.health_check()
        return memory_healthy or redis_healthy


def create_cache_backend(
    backend_type: str = "auto",
    redis_url: str | None = None
) -> CacheBackend:
    """
    Factory function to create a cache backend.

    Args:
        backend_type: Type of backend ("memory", "redis", "tiered", "auto")
        redis_url: Redis URL for Redis or tiered backends

    Returns:
        Configured CacheBackend instance
    """
    if backend_type == "memory":
        return MemoryCacheBackend()
    elif backend_type == "redis":
        if not redis_url:
            logger.warning("Redis URL not provided, falling back to memory")
            return MemoryCacheBackend()
        return RedisCacheBackend(redis_url)
    elif backend_type == "tiered":
        return TieredCacheBackend(redis_url)
    elif backend_type == "auto":
        # Automatically select based on availability
        if redis_url:
            return TieredCacheBackend(redis_url)
        return MemoryCacheBackend()
    else:
        logger.warning(f"Unknown backend type: {backend_type}, using memory")
        return MemoryCacheBackend()
