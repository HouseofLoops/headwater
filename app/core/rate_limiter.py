"""
Rate limiting implementation for the Headwater application.

This module provides rate limiting functionality to protect the API
from abuse and ensure fair usage.

Identity
--------
Requests are bucketed by the ``X-API-Key`` request header when that header
carries a *known* API key, and by client IP otherwise.  The header value is
read directly from the request; it is deliberately **not** resolved through the
``get_current_api_key`` FastAPI dependency, because that dependency is
synchronous and awaiting it raised ``TypeError`` on every request, which was
silently swallowed and downgraded every bucket to per-IP (bug CRT-8).

An *unknown* key never mints its own bucket: otherwise any client could rotate
made-up ``X-API-Key`` values and get an unlimited number of fresh budgets.
Unknown/absent keys fall back to the client IP bucket.

``X-Forwarded-For`` is intentionally ignored: it is attacker-controlled unless
a trusted proxy strips and rewrites it, and honouring it blindly is a trivial
limiter bypass.  Deployments behind a trusted proxy should terminate the header
in the proxy layer (e.g. uvicorn ``--proxy-headers`` with ``--forwarded-allow-ips``)
so that ``request.client.host`` is already correct.

Storage backends
----------------
1. **Redis (required for multi-worker deployments).**  When ``REDIS_URL`` is
   configured, counters live in Redis via the shared async ``RedisManager`` and
   are therefore shared by every worker/instance.

2. **In-memory (single process only).**  A process-local dict.  With
   ``uvicorn --workers N`` this multiplies every configured limit by ``N``.
   ``validate_rate_limit_configuration()`` refuses to start a production
   deployment in that configuration rather than quietly under-enforcing.

Failure policy
--------------
The limiter **fails closed** by default: if the rate limit backend cannot be
consulted (Redis error, misconfigured shared store), the request is rejected
with ``503 Service Unavailable`` instead of being let through uncounted.  A
limiter that fails open silently is exactly the root cause of CRT-8 -- it looked
healthy while enforcing nothing.  Operators who prefer availability over
enforcement can set ``RATE_LIMIT_FAIL_OPEN=true``; the fail-open path is then
taken **loudly** (an ERROR log per occurrence), never silently.

See also: app/core/redis_manager.py for Redis configuration details.
"""
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
import contextlib
import json
import os
import sys
import time
import asyncio
from typing import Dict, Tuple, Optional, Callable, Any, Union, Set
import logging

from app.core.config import get_settings, Settings
from app.core.exceptions import RateLimitExceededError, ServiceUnavailableError

# Configure logger
logger = logging.getLogger(__name__)

# Header carrying the API key (mirrors app.core.auth.api_key_header).
API_KEY_HEADER_NAME = "X-API-Key"

# Key namespaces used in both the in-memory store and Redis.
API_KEY_KEY_PREFIX = "rate_limit:api_key:"
IP_KEY_PREFIX = "rate_limit:ip:"

# Environment variables that uvicorn/gunicorn use to express worker counts.
WORKER_COUNT_ENV_VARS = ("WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS", "WORKERS")

# Environments treated as "production-like" for deployment validation.
PRODUCTION_ENVIRONMENTS = frozenset({"production", "prod", "staging"})

# Default interval, in seconds, between in-memory store cleanup sweeps.
DEFAULT_CLEANUP_INTERVAL = 60

# Thread-safe in-memory storage for rate limiting (single-process fallback)
# Format: {key: (requests_count, window_start_timestamp)}
_rate_limit_store: Dict[str, Tuple[int, float]] = {}

# Lock for thread-safe access to in-memory store
_rate_limit_lock: asyncio.Lock = asyncio.Lock()

# Global cleanup task reference to prevent garbage collection
_cleanup_task: Optional[asyncio.Task] = None

# Shared Redis manager instance (initialized lazily)
_redis_manager: Optional[Any] = None


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class RateLimiterUnavailableError(RuntimeError):
    """Base error for "the limiter could not make a decision"."""


class RateLimiterBackendError(RateLimiterUnavailableError):
    """The rate limit storage backend failed or did not record the request."""


class RateLimiterConfigurationError(RateLimiterUnavailableError):
    """The deployment cannot enforce the configured limits (e.g. workers > 1, no Redis)."""


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def _env_flag(name: str, default: bool = False) -> bool:
    """Read a boolean flag from the process environment."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def fail_open_enabled() -> bool:
    """
    Whether the limiter should allow requests it could not evaluate.

    Defaults to ``False`` (fail closed).  See the module docstring.
    """
    return _env_flag("RATE_LIMIT_FAIL_OPEN", False)


def _worker_count_from_argv(argv: Optional[list] = None) -> int:
    """
    Worker count declared on the command line (``--workers N`` / ``-w N``).

    uvicorn and gunicorn both accept the flag directly, in which case no
    environment variable is set.
    """
    argv = list(sys.argv if argv is None else argv)
    for index, token in enumerate(argv):
        value = None
        if token in ("--workers", "-w") and index + 1 < len(argv):
            value = argv[index + 1]
        elif token.startswith("--workers="):
            value = token.split("=", 1)[1]
        if value is None:
            continue
        try:
            workers = int(value.strip())
        except (TypeError, ValueError):
            continue
        if workers > 0:
            return workers
    return 1


def get_worker_count() -> int:
    """
    Best-effort worker count for this deployment.

    Reads the environment variables uvicorn/gunicorn use to configure worker
    processes, and the ``--workers``/``-w`` command line flag.  Returns 1 when
    nothing is configured or the value is unusable.

    This is best effort: it cannot see horizontal replicas (multiple pods or
    containers behind a load balancer). Those deployments must configure
    REDIS_URL; the in-memory store is only ever correct in a single process.
    """
    counts = [_worker_count_from_argv()]
    for var in WORKER_COUNT_ENV_VARS:
        raw = os.getenv(var)
        if not raw:
            continue
        try:
            workers = int(raw.strip())
        except (TypeError, ValueError):
            logger.warning("Ignoring non-numeric %s=%r when checking worker count", var, raw)
            continue
        if workers > 0:
            counts.append(workers)
            break
    return max(counts)


def _is_production(settings: Settings) -> bool:
    environment = str(getattr(settings, "ENVIRONMENT", "development") or "").strip().lower()
    return environment in PRODUCTION_ENVIRONMENTS


def _redis_url(settings: Settings) -> Optional[str]:
    url = getattr(settings, "REDIS_URL", None)
    return str(url) if url else None


def requires_shared_store(settings: Optional[Settings] = None) -> bool:
    """
    Whether this deployment needs (but lacks) a shared rate limit store.

    True when rate limiting is enabled in a production-like environment with
    more than one worker process and no Redis configured.  In that shape the
    in-memory store gives every worker its own counters, so the effective limit
    is ``RATE_LIMIT_REQUESTS * workers``.
    """
    settings = settings or get_settings()
    if not getattr(settings, "RATE_LIMIT_ENABLED", True):
        return False
    if _redis_url(settings):
        return False
    if not _is_production(settings):
        return False
    return get_worker_count() > 1


def validate_rate_limit_configuration(settings: Optional[Settings] = None) -> None:
    """
    Refuse to run a deployment that cannot enforce its configured limits.

    Raises:
        RateLimiterConfigurationError: if rate limiting is enabled in a
            production-like environment with multiple workers and no Redis.
    """
    settings = settings or get_settings()
    if requires_shared_store(settings):
        workers = get_worker_count()
        raise RateLimiterConfigurationError(
            "Rate limiting is enabled with "
            f"{workers} worker processes but no REDIS_URL is configured. "
            "The in-memory store is per-process, so every configured limit would "
            f"effectively be {workers}x its stated value. "
            "Set REDIS_URL for a shared store, or run a single worker."
        )
    if not _redis_url(settings) and get_worker_count() > 1:
        logger.warning(
            "Rate limiting is using the in-memory store with %d workers; limits are "
            "enforced per process. Configure REDIS_URL for accurate enforcement.",
            get_worker_count(),
        )


async def _get_redis_manager(settings: Optional[Settings] = None) -> Optional[Any]:
    """
    Get the shared Redis manager for rate limiting, or None when Redis is unused.

    Returns:
        Optional[RedisManager]: Redis manager, or None if REDIS_URL is unset.
    """
    global _redis_manager
    if not _redis_url(settings or get_settings()):
        return None
    if _redis_manager is None:
        # Imported lazily so that Redis-free deployments never build the client.
        from app.core.redis_manager import RedisManager

        _redis_manager = await RedisManager.get_instance()
    return _redis_manager


def reset_redis_manager() -> None:
    """Drop the cached Redis manager (used by tests and by reconfiguration)."""
    global _redis_manager
    _redis_manager = None


def reset_rate_limit_state() -> None:
    """
    Drop every piece of module-global limiter state.

    ``_rate_limit_store``, the cleanup task handle and the cached Redis manager
    all live for the lifetime of the process, so anything that can leave
    entries behind between tests can leave them behind in a worker. This is the
    single authoritative reset: one place that knows the full list, rather than
    each caller remembering to clear the dict and hoping that was all of it.

    Cancellation of a running cleanup task is requested but not awaited; use
    ``shutdown_rate_limiting()`` when the task must be joined.
    """
    global _cleanup_task

    _rate_limit_store.clear()
    if _cleanup_task is not None and not _cleanup_task.done():
        _cleanup_task.cancel()
    _cleanup_task = None
    reset_redis_manager()


# ---------------------------------------------------------------------------
# Identity helpers
# ---------------------------------------------------------------------------

def _clean_api_key(value: Any) -> Optional[str]:
    """Normalise one API key token from any configuration source."""
    if not isinstance(value, str):
        return None
    value = value.strip().strip("[]").strip().strip('"').strip("'").strip()
    return value or None


def parse_api_keys_env(raw: Optional[str] = None) -> Set[str]:
    """
    Parse API keys straight from the ``API_KEYS`` environment variable.

    Accepts both encodings this application has used: a JSON array
    (``["a", "b"]``) and a comma-separated list (``a,b``).

    This exists because ``Settings.API_KEYS`` and ``AuthSettings.API_KEYS``
    disagree about which of those two forms they can decode -- pydantic-settings
    JSON-decodes list fields before ``assemble_api_keys`` ever runs. Bucket
    identity must not depend on which parser happened to win, or the limiter
    silently degrades to per-IP bucketing again (the CRT-8 failure mode).
    """
    raw = os.getenv("API_KEYS") if raw is None else raw
    keys: Set[str] = set()

    if raw and raw.strip():
        stripped = raw.strip()
        decoded = None
        if stripped.startswith("["):
            try:
                decoded = json.loads(stripped)
            except ValueError:
                decoded = None
        tokens = decoded if isinstance(decoded, list) else stripped.split(",")
        for token in tokens:
            key = _clean_api_key(token)
            if key:
                keys.add(key)

    # Backwards-compatible single-key variable, as honoured by app.core.auth.
    single = _clean_api_key(os.getenv("API_KEY"))
    if single:
        keys.add(single)

    return keys


def _configured_api_keys(settings: Optional[Settings] = None) -> Set[str]:
    """
    All API keys this process considers valid.

    Unions every configuration source -- application settings, the set
    maintained by ``app.core.auth``, and the raw environment -- so that the
    bucket a request lands in does not depend on which source parsed the value.
    """
    settings = settings or get_settings()
    keys: Set[str] = parse_api_keys_env()

    for key in getattr(settings, "API_KEYS", None) or []:
        cleaned = _clean_api_key(key)
        if cleaned:
            keys.add(cleaned)

    # Read (never mutate) the auth module's key set.
    try:
        from app.core import auth as _auth

        for key in getattr(_auth, "_api_keys_set", None) or set():
            cleaned = _clean_api_key(key)
            if cleaned:
                keys.add(cleaned)
    except ImportError:  # pragma: no cover - auth is always importable in-app
        logger.warning("app.core.auth unavailable while resolving API keys")

    return keys


def extract_api_key(request: Request) -> Optional[str]:
    """
    Read the API key header from a request without invoking auth dependencies.

    Returns:
        Optional[str]: The header value, or None when absent/blank/non-string.
    """
    headers = getattr(request, "headers", None)
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return None
    value = getter(API_KEY_HEADER_NAME)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _hash_identity(value: str) -> str:
    """
    Hash an identity before it becomes part of a storage key.

    Rate limit keys end up in Redis and in debug logs; API keys are secrets, so
    only a digest of the key is stored. The digest is KEYED (see
    app.core.identity): a bare sha256 of a short, human-chosen API key is
    recoverable from a leaked Redis dump by hashing a wordlist. A slow password
    KDF would be the wrong fix here -- this runs on every request.
    """
    from app.core.identity import keyed_digest

    return keyed_digest(value)


def client_host(request: Request) -> str:
    """Return the peer address for a request, or ``"unknown"``."""
    client = getattr(request, "client", None)
    host = getattr(client, "host", None) if client is not None else None
    return host if isinstance(host, str) and host else "unknown"


def build_rate_limit_key(api_key: Optional[str] = None, host: Optional[str] = None) -> str:
    """
    Build the storage key for an identity.

    Args:
        api_key: A *validated* API key, or None.
        host: The client host, used when no validated API key is available.
    """
    if api_key:
        return f"{API_KEY_KEY_PREFIX}{_hash_identity(api_key)}"
    return f"{IP_KEY_PREFIX}{host or 'unknown'}"


class RateLimiter:
    """
    Rate limiter for API requests.

    Buckets requests by validated API key when present, otherwise by client IP.
    Uses Redis when configured (shared across workers), falling back to a
    process-local in-memory store.

    Limits are resolved from settings on every request unless explicitly
    overridden in the constructor, so a limiter created at import time still
    reflects later configuration changes.
    """

    def __init__(
        self,
        requests: Optional[int] = None,
        timeframe: Optional[int] = None,  # seconds
        settings: Optional[Settings] = None
    ):
        """
        Initialize the rate limiter.

        Args:
            requests: Maximum requests per timeframe. None -> read from settings.
            timeframe: Timeframe in seconds. None -> read from settings.
            settings: Optional settings instance. None -> read lazily.
        """
        self._requests_override = requests
        self._timeframe_override = timeframe
        self._settings_override = settings

    @property
    def settings(self) -> Settings:
        """
        The settings instance backing this limiter.

        Raises:
            RateLimiterConfigurationError: if settings cannot be loaded. A
                limiter that cannot read its own configuration must not pretend
                to enforce anything.
        """
        if self._settings_override is not None:
            return self._settings_override
        try:
            return get_settings()
        except Exception as exc:  # noqa: BLE001 - re-raised as a typed error
            raise RateLimiterConfigurationError(
                f"Rate limit settings could not be loaded: {exc}"
            ) from exc

    @property
    def requests(self) -> int:
        """Maximum number of requests allowed per timeframe."""
        if self._requests_override is not None:
            return self._requests_override
        return int(getattr(self.settings, "RATE_LIMIT_REQUESTS", 100) or 100)

    @property
    def timeframe(self) -> int:
        """Length of the rate limit window, in seconds."""
        if self._timeframe_override is not None:
            return self._timeframe_override
        return int(getattr(self.settings, "RATE_LIMIT_TIMEFRAME", 3600) or 3600)

    @property
    def enabled(self) -> bool:
        """Whether rate limiting is turned on."""
        return bool(getattr(self.settings, "RATE_LIMIT_ENABLED", True))

    async def _get_rate_limit_key(self, request: Request) -> str:
        """
        Get the key to use for rate limiting.

        Uses the API key from the ``X-API-Key`` header when that key is one of
        the configured keys; otherwise falls back to the client IP.  An unknown
        key must not create its own bucket -- that would let a client mint fresh
        budgets at will -- so it shares the IP bucket.

        Args:
            request: The request object

        Returns:
            str: The rate limit key
        """
        api_key = extract_api_key(request)
        if api_key and api_key in _configured_api_keys(self.settings):
            return build_rate_limit_key(api_key=api_key)
        if api_key:
            logger.debug("Unrecognised API key presented; rate limiting by client address")
        return build_rate_limit_key(host=client_host(request))

    async def is_rate_limited(self, request: Request) -> Tuple[bool, Dict[str, Any]]:
        """
        Check if a request is rate limited.

        Args:
            request: The request object

        Returns:
            Tuple[bool, Dict[str, Any]]: (is_limited, rate_limit_info)

        Raises:
            RateLimiterUnavailableError: if no usable backend could evaluate the
                request.  Callers decide whether that fails open or closed.
        """
        if not self.enabled:
            return False, {}

        # Snapshot the limits so a settings change mid-request cannot skew them.
        limit = self.requests
        timeframe = self.timeframe

        key = await self._get_rate_limit_key(request)

        redis_manager = await _get_redis_manager(self.settings)
        if redis_manager is not None:
            if getattr(redis_manager, "is_available", False):
                return await self._check_rate_limit_redis(key, redis_manager, limit, timeframe)
            raise RateLimiterBackendError(
                "REDIS_URL is configured but the Redis connection is unavailable"
            )

        # No Redis configured: the in-memory store is only correct in a single
        # process. Refuse rather than silently enforce limit * workers.
        if requires_shared_store(self.settings):
            raise RateLimiterConfigurationError(
                f"Rate limiting requires a shared store: {get_worker_count()} workers "
                "are configured without REDIS_URL"
            )

        return await self._check_rate_limit_memory(key, limit, timeframe)

    async def _check_rate_limit_redis(
        self,
        key: str,
        redis_manager: Any,
        limit: int,
        timeframe: int,
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Check rate limit using Redis storage (shared across workers).

        Args:
            key: The rate limit key
            redis_manager: RedisManager instance
            limit: Maximum requests per window
            timeframe: Window length in seconds

        Returns:
            Tuple[bool, Dict[str, Any]]: (is_limited, rate_limit_info)

        Raises:
            RateLimiterBackendError: if Redis errored or did not count the request.
        """
        try:
            allowed, current_count, reset = await redis_manager.rate_limit_check(
                key, limit, timeframe
            )
        except Exception as exc:  # noqa: BLE001 - re-raised as a typed error below
            raise RateLimiterBackendError(f"Redis rate limit check failed: {exc}") from exc

        # RedisManager.rate_limit_check returns (True, 0, 0) when it could not
        # reach Redis. A counted request always yields current_count >= 1, so a
        # zero count means this request was NOT recorded -- do not treat that as
        # an allow.
        if current_count is None or current_count < 1:
            raise RateLimiterBackendError(
                "Redis rate limit check did not record the request "
                "(Redis unreachable or returned no count)"
            )

        return (not allowed), self._get_rate_limit_headers(
            current_count, limit, timeframe, reset_seconds=reset
        )

    async def _check_rate_limit_memory(
        self,
        key: str,
        limit: Optional[int] = None,
        timeframe: Optional[int] = None,
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Check rate limit using the process-local in-memory store.

        Args:
            key: The rate limit key
            limit: Maximum requests per window (defaults to the configured limit)
            timeframe: Window length in seconds (defaults to the configured window)

        Returns:
            Tuple[bool, Dict[str, Any]]: (is_limited, rate_limit_info)
        """
        limit = self.requests if limit is None else limit
        timeframe = self.timeframe if timeframe is None else timeframe
        now = time.time()

        async with _rate_limit_lock:
            entry = _rate_limit_store.get(key)

            if entry is None or now - entry[1] > timeframe:
                # First request for this key, or the window has expired.
                _rate_limit_store[key] = (1, now)
                return False, self._get_rate_limit_headers(1, limit, timeframe, now)

            requests_count, window_start = entry
            new_count = requests_count + 1
            _rate_limit_store[key] = (new_count, window_start)

        return (
            new_count > limit,
            self._get_rate_limit_headers(new_count, limit, timeframe, window_start),
        )

    def _get_rate_limit_headers(
        self,
        current: int,
        limit: int,
        timeframe: int,
        window_start: Optional[float] = None,
        reset_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Get rate limit headers for the response.

        Args:
            current: Current request count
            limit: Maximum request count
            timeframe: Timeframe in seconds
            window_start: Window start timestamp
            reset_seconds: Seconds until reset, when the backend reports it directly

        Returns:
            Dict[str, Any]: Rate limit headers and info
        """
        if reset_seconds is None:
            now = time.time()
            window_start = window_start if window_start is not None else now
            reset_seconds = int(window_start + timeframe - now)
        reset = max(0, int(reset_seconds))

        return {
            "headers": {
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": str(max(0, limit - current)),
                "X-RateLimit-Reset": str(reset)
            },
            "current": current,
            "limit": limit,
            "reset": reset
        }

    @staticmethod
    def _retry_after(reset: int) -> str:
        """Retry-After value for a 429/503 response (always at least 1 second)."""
        return str(max(1, int(reset)))

    def _too_many_requests(self, rate_limit_info: Dict[str, Any]) -> Dict[str, Any]:
        """Build the headers for a 429 response, including Retry-After."""
        headers = dict(rate_limit_info["headers"])
        headers["Retry-After"] = self._retry_after(rate_limit_info["reset"])
        return headers

    async def limit(
        self,
        request: Request,
        call_next: Optional[Callable] = None
    ) -> Union[Response, Any]:
        """
        Apply rate limiting to a request.

        This method can be used as a middleware or a dependency.

        Args:
            request: The request object
            call_next: Optional next middleware or route handler

        Returns:
            Union[Response, Any]: Response or next middleware result

        Raises:
            RateLimitExceededError: If the request is rate limited (dependency use)
            ServiceUnavailableError: If the limiter could not evaluate the request
                and fail-open is not enabled (dependency use)
        """
        try:
            enabled = self.enabled
            if enabled:
                is_limited, rate_limit_info = await self.is_rate_limited(request)
        except RateLimiterUnavailableError as exc:
            return await self._handle_unavailable(request, exc, call_next)

        if not enabled:
            if call_next:
                return await call_next(request)
            return None

        if is_limited:
            logger.warning(
                "Rate limit exceeded",
                extra={
                    "path": str(getattr(getattr(request, "url", None), "path", "")),
                    "method": getattr(request, "method", ""),
                    "client_host": client_host(request),
                    "rate_limit_info": rate_limit_info
                }
            )

            headers = self._too_many_requests(rate_limit_info)
            detail = (
                f"Rate limit exceeded. Try again in {rate_limit_info['reset']} seconds."
            )

            # If used as a middleware, return a response
            if call_next:
                return JSONResponse(
                    status_code=429,
                    content={
                        "type": "https://headwater.com/problems/rate_limit_exceeded",
                        "title": "Too Many Requests",
                        "status": 429,
                        "detail": detail,
                        "limit": rate_limit_info["limit"],
                        "reset": rate_limit_info["reset"]
                    },
                    headers=headers
                )

            # If used as a dependency, raise an exception
            raise RateLimitExceededError(
                detail=detail,
                headers=headers,
                reset=rate_limit_info["reset"],
                limit=rate_limit_info["limit"]
            )

        # Add rate limit headers to the response
        if call_next:
            response = await call_next(request)

            for header, value in rate_limit_info["headers"].items():
                response.headers[header] = value

            return response

        # If used as a dependency, return None
        return None

    async def _handle_unavailable(
        self,
        request: Request,
        exc: RateLimiterUnavailableError,
        call_next: Optional[Callable],
    ) -> Union[Response, Any]:
        """
        Apply the configured failure policy when the limiter cannot decide.

        Fails closed (503) by default; fails open only when
        ``RATE_LIMIT_FAIL_OPEN`` is set, and then noisily.
        """
        path = str(getattr(getattr(request, "url", None), "path", ""))

        if fail_open_enabled():
            logger.error(
                "Rate limiter unavailable (%s); RATE_LIMIT_FAIL_OPEN is set so this "
                "request to %s is being allowed UNCOUNTED",
                exc,
                path,
                exc_info=True,
            )
            if call_next:
                return await call_next(request)
            return None

        logger.error(
            "Rate limiter unavailable (%s); rejecting request to %s (fail closed). "
            "Set RATE_LIMIT_FAIL_OPEN=true to prefer availability over enforcement.",
            exc,
            path,
            exc_info=True,
        )

        detail = "Rate limiting is temporarily unavailable. Please retry shortly."
        headers = {"Retry-After": self._retry_after(1)}

        if call_next:
            return JSONResponse(
                status_code=503,
                content={
                    "type": "https://headwater.com/problems/service_unavailable",
                    "title": "Service Unavailable",
                    "status": 503,
                    "detail": detail,
                },
                headers=headers,
            )

        raise ServiceUnavailableError(detail=detail, headers=headers)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Middleware for rate limiting requests.

    This middleware applies rate limiting to all requests based on
    API key or IP address.
    """

    def __init__(
        self,
        app: ASGIApp,
        requests: Optional[int] = None,
        timeframe: Optional[int] = None,  # seconds
        settings: Optional[Settings] = None
    ):
        """
        Initialize the middleware.

        Args:
            app: The ASGI application
            requests: Maximum requests per timeframe (None -> from settings)
            timeframe: Timeframe in seconds (None -> from settings)
            settings: Optional settings instance
        """
        super().__init__(app)
        self.limiter = RateLimiter(requests, timeframe, settings)

    async def dispatch(
        self, request: Request, call_next: Callable
    ) -> Response:
        """
        Apply rate limiting to the request.

        Args:
            request: The request object
            call_next: The next middleware or route handler

        Returns:
            Response: The response
        """
        return await self.limiter.limit(request, call_next)


# Create a global rate limiter instance (limits resolved per request)
limiter = RateLimiter()


# Dependency for rate limiting
async def rate_limit(request: Request):
    """
    Dependency for rate limiting.

    This dependency can be used in FastAPI routes to apply rate limiting.

    Args:
        request: The request object

    Raises:
        RateLimitExceededError: If the request is rate limited
        ServiceUnavailableError: If the limiter cannot evaluate the request and
            fail-open is not enabled
    """
    await limiter.limit(request)


# ---------------------------------------------------------------------------
# In-memory store maintenance
# ---------------------------------------------------------------------------

async def purge_expired_entries(
    now: Optional[float] = None,
    timeframe: Optional[int] = None,
) -> int:
    """
    Remove expired entries from the in-memory rate limit store.

    Args:
        now: Timestamp to evaluate against (defaults to the current time)
        timeframe: Window length in seconds (defaults to the configured window)

    Returns:
        int: Number of entries removed
    """
    now = time.time() if now is None else now
    if timeframe is None:
        timeframe = int(getattr(get_settings(), "RATE_LIMIT_TIMEFRAME", 3600) or 3600)

    async with _rate_limit_lock:
        expired_keys = [
            key for key, (_, window_start) in _rate_limit_store.items()
            if now - window_start > timeframe
        ]
        for key in expired_keys:
            del _rate_limit_store[key]

    if expired_keys:
        logger.debug("Cleaned up %d expired rate limit entries", len(expired_keys))
    return len(expired_keys)


async def cleanup_rate_limit_store(interval: int = DEFAULT_CLEANUP_INTERVAL) -> None:
    """
    Periodically clean up expired rate limit entries.

    Runs until cancelled.  ``asyncio.CancelledError`` is propagated so the task
    is genuinely cancellable; every other error is logged and the loop
    continues, because an unbounded store is a slow memory leak.

    Args:
        interval: Seconds between sweeps
    """
    while True:
        try:
            await purge_expired_entries()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - keep the janitor alive
            logger.error("Error in rate limit store cleanup: %s", exc, exc_info=True)

        await asyncio.sleep(interval)


def start_cleanup_task(interval: int = DEFAULT_CLEANUP_INTERVAL) -> asyncio.Task:
    """
    Validate the deployment and start the rate limit store cleanup task.

    Call this once at application startup (e.g. from the FastAPI lifespan
    handler).  The task reference is stored globally to prevent garbage
    collection, and calling this repeatedly is a no-op while a task is running.

    Args:
        interval: Seconds between cleanup sweeps

    Returns:
        asyncio.Task: The running cleanup task

    Raises:
        RateLimiterConfigurationError: if this deployment cannot enforce its
            configured limits (multiple workers without Redis in production).
            Startup must fail rather than silently under-enforce.
    """
    global _cleanup_task

    validate_rate_limit_configuration()

    if _cleanup_task is None or _cleanup_task.done():
        _cleanup_task = asyncio.create_task(cleanup_rate_limit_store(interval))
        logger.info("Rate limit cleanup task started (every %ss)", interval)
    return _cleanup_task


def stop_cleanup_task() -> None:
    """Request cancellation of the rate limit store cleanup task."""
    global _cleanup_task
    if _cleanup_task is not None and not _cleanup_task.done():
        _cleanup_task.cancel()
        logger.info("Rate limit cleanup task cancellation requested")


async def shutdown_rate_limiting() -> None:
    """
    Cancel the cleanup task and wait for it to finish.

    Safe to call from a FastAPI lifespan shutdown even if no task was started.
    """
    global _cleanup_task
    task = _cleanup_task
    stop_cleanup_task()
    if task is not None:
        with contextlib.suppress(asyncio.CancelledError):
            await task
        logger.info("Rate limit cleanup task stopped")
    _cleanup_task = None
