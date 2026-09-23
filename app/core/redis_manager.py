"""
Shared Redis connection manager for the Headwater application.

This module provides a centralized, async-compatible Redis connection manager
that can be used by caching, rate limiting, and other Redis-dependent features.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from redis.asyncio import Redis
from redis.exceptions import ConnectionError, RedisError, TimeoutError

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class RedisManager:
    """
    Centralized Redis connection manager with async support.

    This class provides:
    - Async Redis operations
    - Connection pooling
    - Automatic reconnection
    - Health checking
    - Graceful shutdown
    """

    _instance: RedisManager | None = None
    _lock: asyncio.Lock = asyncio.Lock()

    def __init__(self):
        """Initialize the Redis manager."""
        self.settings = get_settings()
        self._client: Redis | None = None
        self._initialized = False
        self._connection_error_count = 0
        self._max_connection_errors = 5

    @classmethod
    async def get_instance(cls) -> RedisManager:
        """
        Get the singleton instance of RedisManager.

        Returns:
            RedisManager: The singleton instance
        """
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
                    await cls._instance._initialize()
        return cls._instance

    async def _initialize(self) -> None:
        """Initialize the Redis connection."""
        if self._initialized:
            return

        redis_url = getattr(self.settings, "REDIS_URL", None)
        if not redis_url:
            logger.warning("REDIS_URL not configured. Redis features disabled.")
            return

        try:
            # Create async Redis client with connection pool
            self._client = aioredis.from_url(
                str(redis_url),
                encoding="utf-8",
                decode_responses=True,
                # Pin RESP2 explicitly. redis-py 8.x flips DEFAULT_RESP_VERSION
                # from 2 to 3, which changes reply types under this client
                # without any test catching it (the suite runs with
                # REDIS_URL unset, so this path is never exercised in CI).
                # Being explicit makes the 8.x upgrade a deliberate choice
                # rather than a silent behaviour change.
                protocol=2,
                max_connections=20,
                socket_connect_timeout=5.0,
                socket_timeout=5.0,
                retry_on_timeout=True,
                health_check_interval=30,
            )

            # Test connection
            await self._client.ping()
            self._initialized = True
            self._connection_error_count = 0
            logger.info("Redis async connection initialized successfully")

        except (ConnectionError, TimeoutError) as e:
            logger.error(f"Failed to connect to Redis: {e}")
            self._client = None
        except Exception as e:
            logger.error(f"Unexpected error initializing Redis: {e}")
            self._client = None

    @property
    def is_available(self) -> bool:
        """Check if Redis is available."""
        return self._client is not None and self._initialized

    async def get_client(self) -> Redis | None:
        """
        Get the Redis client, attempting reconnection if necessary.

        Returns:
            Optional[Redis]: The Redis client or None if unavailable
        """
        if not self._client and self._connection_error_count < self._max_connection_errors:
            await self._initialize()
        return self._client

    async def health_check(self) -> dict:
        """
        Perform a health check on the Redis connection.

        Returns:
            dict: Health check results with status and latency
        """
        if not self._client:
            return {"status": "unavailable", "latency_ms": None, "error": "Redis client not initialized"}

        try:
            import time

            start = time.perf_counter()
            await self._client.ping()
            latency = (time.perf_counter() - start) * 1000

            return {"status": "healthy", "latency_ms": round(latency, 2), "error": None}
        except Exception as e:
            return {"status": "unhealthy", "latency_ms": None, "error": str(e)}

    # ==========================================================================
    # Async Redis Operations
    # ==========================================================================

    async def get(self, key: str) -> str | None:
        """
        Get a value from Redis.

        Args:
            key: The key to retrieve

        Returns:
            Optional[str]: The value or None
        """
        client = await self.get_client()
        if not client:
            return None

        try:
            return await client.get(key)
        except RedisError as e:
            logger.error(f"Redis GET error for key {key}: {e}")
            self._handle_connection_error()
            return None

    async def set(self, key: str, value: str, ttl: int | None = None) -> bool:
        """
        Set a value in Redis.

        Args:
            key: The key to set
            value: The value to store
            ttl: Optional TTL in seconds

        Returns:
            bool: True if successful
        """
        client = await self.get_client()
        if not client:
            return False

        try:
            if ttl:
                await client.setex(key, ttl, value)
            else:
                await client.set(key, value)
            return True
        except RedisError as e:
            logger.error(f"Redis SET error for key {key}: {e}")
            self._handle_connection_error()
            return False

    async def delete(self, *keys: str) -> int:
        """
        Delete one or more keys from Redis.

        Args:
            *keys: Keys to delete

        Returns:
            int: Number of keys deleted
        """
        client = await self.get_client()
        if not client or not keys:
            return 0

        try:
            return await client.delete(*keys)
        except RedisError as e:
            logger.error(f"Redis DELETE error: {e}")
            self._handle_connection_error()
            return 0

    async def exists(self, *keys: str) -> int:
        """
        Check if keys exist in Redis.

        Args:
            *keys: Keys to check

        Returns:
            int: Number of keys that exist
        """
        client = await self.get_client()
        if not client or not keys:
            return 0

        try:
            return await client.exists(*keys)
        except RedisError as e:
            logger.error(f"Redis EXISTS error: {e}")
            self._handle_connection_error()
            return 0

    async def incr(self, key: str) -> int | None:
        """
        Increment a counter in Redis.

        Args:
            key: The key to increment

        Returns:
            Optional[int]: The new value or None
        """
        client = await self.get_client()
        if not client:
            return None

        try:
            return await client.incr(key)
        except RedisError as e:
            logger.error(f"Redis INCR error for key {key}: {e}")
            self._handle_connection_error()
            return None

    async def expire(self, key: str, ttl: int) -> bool:
        """
        Set expiration on a key.

        Args:
            key: The key to expire
            ttl: TTL in seconds

        Returns:
            bool: True if successful
        """
        client = await self.get_client()
        if not client:
            return False

        try:
            return await client.expire(key, ttl)
        except RedisError as e:
            logger.error(f"Redis EXPIRE error for key {key}: {e}")
            self._handle_connection_error()
            return False

    async def ttl(self, key: str) -> int:
        """
        Get TTL for a key.

        Args:
            key: The key to check

        Returns:
            int: TTL in seconds, -1 if no TTL, -2 if key doesn't exist
        """
        client = await self.get_client()
        if not client:
            return -2

        try:
            return await client.ttl(key)
        except RedisError as e:
            logger.error(f"Redis TTL error for key {key}: {e}")
            self._handle_connection_error()
            return -2

    async def keys(self, pattern: str) -> list:
        """
        Get keys matching a pattern.

        Note: Use with caution in production - prefer SCAN for large keyspaces.

        Args:
            pattern: Key pattern to match

        Returns:
            list: Matching keys
        """
        client = await self.get_client()
        if not client:
            return []

        try:
            return await client.keys(pattern)
        except RedisError as e:
            logger.error(f"Redis KEYS error for pattern {pattern}: {e}")
            self._handle_connection_error()
            return []

    async def pipeline(self):
        """
        Get a Redis pipeline for batch operations.

        Returns:
            Pipeline or None if unavailable
        """
        client = await self.get_client()
        if not client:
            return None
        return client.pipeline()

    # ==========================================================================
    # Rate Limiting Support
    # ==========================================================================

    async def rate_limit_check(self, key: str, limit: int, window: int) -> tuple[bool, int, int]:
        """
        Atomic rate limit check using Redis.

        Args:
            key: The rate limit key
            limit: Maximum requests allowed
            window: Time window in seconds

        Returns:
            tuple: (allowed, current_count, reset_time)
        """
        client = await self.get_client()
        if not client:
            # Fallback: always allow if Redis unavailable
            return True, 0, 0

        try:
            pipe = client.pipeline()
            pipe.incr(key)
            pipe.ttl(key)
            results = await pipe.execute()

            current = results[0]
            ttl = results[1]

            # Set expiry if this is a new key
            if ttl == -1:
                await client.expire(key, window)
                ttl = window

            allowed = current <= limit
            reset_time = max(0, ttl)

            return allowed, current, reset_time

        except RedisError as e:
            logger.error(f"Redis rate limit error for key {key}: {e}")
            self._handle_connection_error()
            return True, 0, 0

    # ==========================================================================
    # Connection Management
    # ==========================================================================

    def _handle_connection_error(self) -> None:
        """Handle a connection error by incrementing the error count."""
        self._connection_error_count += 1
        if self._connection_error_count >= self._max_connection_errors:
            logger.warning(
                f"Redis connection error limit reached ({self._max_connection_errors}). "
                "Redis operations will be disabled."
            )

    async def reset_connection(self) -> bool:
        """
        Reset the Redis connection.

        Returns:
            bool: True if reconnection successful
        """
        if self._client:
            try:
                await self._client.close()
            except Exception:
                pass

        self._client = None
        self._initialized = False
        self._connection_error_count = 0

        await self._initialize()
        return self._initialized

    async def close(self) -> None:
        """Close the Redis connection gracefully."""
        if self._client:
            try:
                await self._client.close()
                logger.info("Redis connection closed")
            except Exception as e:
                logger.error(f"Error closing Redis connection: {e}")
            finally:
                self._client = None
                self._initialized = False

    @classmethod
    async def shutdown(cls) -> None:
        """Shutdown the singleton instance."""
        if cls._instance:
            await cls._instance.close()
            cls._instance = None


# =============================================================================
# Convenience Functions
# =============================================================================


async def get_redis() -> Redis | None:
    """
    Get the Redis client.

    Returns:
        Optional[Redis]: The Redis client or None
    """
    manager = await RedisManager.get_instance()
    return await manager.get_client()


async def redis_health_check() -> dict:
    """
    Perform a Redis health check.

    Returns:
        dict: Health check results
    """
    manager = await RedisManager.get_instance()
    return await manager.health_check()


@asynccontextmanager
async def redis_pipeline():
    """
    Context manager for Redis pipeline operations.

    Usage:
        async with redis_pipeline() as pipe:
            pipe.set("key1", "value1")
            pipe.set("key2", "value2")
            results = await pipe.execute()
    """
    manager = await RedisManager.get_instance()
    pipe = await manager.pipeline()
    if pipe:
        try:
            yield pipe
        finally:
            pass  # Pipeline auto-closes
    else:
        yield None
