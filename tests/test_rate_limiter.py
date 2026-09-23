"""
Unit tests for app.core.rate_limiter.

These cover the CRT-8 regressions:
  * the bucket key is derived from the API key header, never by awaiting the
    synchronous ``get_current_api_key`` dependency (which silently downgraded
    every request to per-IP bucketing);
  * an unknown API key cannot mint a fresh budget;
  * exceeding the limit returns 429 with a correct ``Retry-After``;
  * the cleanup task actually removes expired entries and is cancellable;
  * the Redis backend enforces one budget across simulated workers;
  * a multi-worker production deployment without Redis refuses to start;
  * backend failures fail closed by default (and fail open only when the
    operator explicitly asks for it).
"""

import asyncio
import contextlib
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.exceptions import RateLimitExceededError, ServiceUnavailableError
from app.core.rate_limiter import (
    API_KEY_KEY_PREFIX,
    IP_KEY_PREFIX,
    RateLimiter,
    RateLimiterBackendError,
    RateLimiterConfigurationError,
    RateLimitMiddleware,
    _rate_limit_store,
    build_rate_limit_key,
    cleanup_rate_limit_store,
    get_worker_count,
    limiter,
    parse_api_keys_env,
    purge_expired_entries,
    rate_limit,
    requires_shared_store,
    reset_rate_limit_state,
    shutdown_rate_limiting,
    start_cleanup_task,
    stop_cleanup_task,
    validate_rate_limit_configuration,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def settings_stub(**overrides):
    """Build a settings-like object for the limiter."""
    base = dict(
        RATE_LIMIT_ENABLED=True,
        RATE_LIMIT_REQUESTS=100,
        RATE_LIMIT_TIMEFRAME=3600,
        REDIS_URL=None,
        ENVIRONMENT="development",
        API_KEYS=[],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def make_request(path="/test", method="GET", api_key=None, host="127.0.0.1"):
    """Build a real Starlette Request (not a MagicMock) for key derivation."""
    headers = []
    if api_key is not None:
        headers.append((b"x-api-key", api_key.encode()))
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": (host, 12345) if host else None,
        "server": ("testserver", 80),
    }
    return Request(scope)


class FakePipeline:
    """Minimal async Redis pipeline supporting incr/ttl/execute."""

    def __init__(self, client):
        self._client = client
        self._ops = []

    def incr(self, key):
        self._ops.append(("incr", key))
        return self

    def ttl(self, key):
        self._ops.append(("ttl", key))
        return self

    async def execute(self):
        return [self._client.incr_sync(key) if op == "incr" else self._client.ttl_sync(key) for op, key in self._ops]


class FakeRedisClient:
    """
    Tiny in-process stand-in for redis.asyncio.Redis.

    Implements only what RedisManager.rate_limit_check needs. Used instead of a
    live Redis server (and instead of fakeredis, which is not a dependency).
    """

    def __init__(self):
        self.counters = {}
        self.expiry = {}
        self.now = time.time()

    # --- sync internals used by the pipeline -------------------------------
    def _expire_if_due(self, key):
        deadline = self.expiry.get(key)
        if deadline is not None and self.now >= deadline:
            self.counters.pop(key, None)
            self.expiry.pop(key, None)

    def incr_sync(self, key):
        self._expire_if_due(key)
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    def ttl_sync(self, key):
        self._expire_if_due(key)
        if key not in self.counters:
            return -2
        deadline = self.expiry.get(key)
        if deadline is None:
            return -1
        return max(0, int(deadline - self.now))

    # --- async surface -----------------------------------------------------
    def pipeline(self):
        return FakePipeline(self)

    async def expire(self, key, ttl):
        self.expiry[key] = self.now + ttl
        return True

    async def ping(self):
        return True


def fake_redis_manager(client=None):
    """A real RedisManager wired to a fake client, so real code paths run."""
    from app.core.redis_manager import RedisManager

    manager = RedisManager()
    manager._client = client or FakeRedisClient()
    manager._initialized = True
    return manager


ISOLATED_ENV_VARS = (
    "WEB_CONCURRENCY",
    "UVICORN_WORKERS",
    "GUNICORN_WORKERS",
    "WORKERS",
    "RATE_LIMIT_FAIL_OPEN",
    "RATE_LIMIT_ENABLED",
    "RATE_LIMIT_REQUESTS",
    "RATE_LIMIT_TIMEFRAME",
    "REDIS_URL",
    "API_KEYS",
    "API_KEY",
)


@pytest.fixture(autouse=True)
def _isolate_rate_limiter(monkeypatch):
    """
    Guarantee isolation by construction, not by test ordering.

    Every limiter global (the store, the cleanup task handle, the cached Redis
    manager) and every environment variable the limiter reads is reset around
    each test, so this module cannot leave residue for the integration tests --
    or for the next test in this file.
    """
    reset_rate_limit_state()
    for var in ISOLATED_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    yield
    reset_rate_limit_state()
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Construction / configuration
# ---------------------------------------------------------------------------


class TestRateLimiter:
    """Test cases for RateLimiter configuration."""

    def test_init_with_settings(self):
        limiter_ = RateLimiter(settings=settings_stub(RATE_LIMIT_REQUESTS=50, RATE_LIMIT_TIMEFRAME=1800))

        assert limiter_.enabled is True
        assert limiter_.requests == 50
        assert limiter_.timeframe == 1800

    def test_init_defaults(self):
        with patch("app.core.rate_limiter.get_settings", return_value=settings_stub()):
            limiter_ = RateLimiter()

            assert limiter_.enabled is True
            assert limiter_.requests == 100
            assert limiter_.timeframe == 3600

    def test_init_disabled(self):
        limiter_ = RateLimiter(settings=settings_stub(RATE_LIMIT_ENABLED=False))
        assert limiter_.enabled is False

    def test_explicit_overrides_win_over_settings(self):
        limiter_ = RateLimiter(requests=7, timeframe=11, settings=settings_stub(RATE_LIMIT_REQUESTS=100))
        assert limiter_.requests == 7
        assert limiter_.timeframe == 11

    def test_limits_are_reread_from_settings_per_request(self):
        """A limiter built at import time must see later settings changes."""
        stub = settings_stub(RATE_LIMIT_REQUESTS=5)
        with patch("app.core.rate_limiter.get_settings", return_value=stub):
            limiter_ = RateLimiter()
            assert limiter_.requests == 5
            stub.RATE_LIMIT_REQUESTS = 9
            assert limiter_.requests == 9


# ---------------------------------------------------------------------------
# Key derivation  (bug #1)
# ---------------------------------------------------------------------------


class TestRateLimitKey:
    """The key must come from the API key header, not from an awaited dependency."""

    @pytest.mark.asyncio
    async def test_key_uses_api_key_when_present(self):
        limiter_ = RateLimiter(settings=settings_stub(API_KEYS=["secret-key"]))
        key = await limiter_._get_rate_limit_key(make_request(api_key="secret-key"))

        assert key.startswith(API_KEY_KEY_PREFIX)
        assert key == build_rate_limit_key(api_key="secret-key")
        # The IP must play no part when a valid key is presented.
        assert "127.0.0.1" not in key
        assert not key.startswith(IP_KEY_PREFIX)

    @pytest.mark.asyncio
    async def test_key_never_awaits_get_current_api_key(self):
        """
        Regression for CRT-8: the old code did ``await get_current_api_key(...)``
        on a synchronous function, so every request raised TypeError, got
        swallowed by a bare except, and fell back to per-IP bucketing.
        """
        import app.core.auth as auth_module

        called = []

        def _tripwire(*args, **kwargs):
            called.append(args)
            raise AssertionError("rate limiter must not call get_current_api_key")

        with patch.object(auth_module, "get_current_api_key", _tripwire):
            limiter_ = RateLimiter(settings=settings_stub(API_KEYS=["secret-key"]))
            key = await limiter_._get_rate_limit_key(make_request(api_key="secret-key"))

        assert called == []
        assert key == build_rate_limit_key(api_key="secret-key")

    @pytest.mark.asyncio
    async def test_distinct_api_keys_get_distinct_buckets(self):
        limiter_ = RateLimiter(settings=settings_stub(API_KEYS=["key-a", "key-b"]))
        key_a = await limiter_._get_rate_limit_key(make_request(api_key="key-a"))
        key_b = await limiter_._get_rate_limit_key(make_request(api_key="key-b"))

        assert key_a != key_b
        assert key_a.startswith(API_KEY_KEY_PREFIX)
        assert key_b.startswith(API_KEY_KEY_PREFIX)

    @pytest.mark.asyncio
    async def test_same_api_key_from_different_ips_shares_a_bucket(self):
        limiter_ = RateLimiter(settings=settings_stub(API_KEYS=["key-a"]))
        key_1 = await limiter_._get_rate_limit_key(make_request(api_key="key-a", host="10.0.0.1"))
        key_2 = await limiter_._get_rate_limit_key(make_request(api_key="key-a", host="10.0.0.2"))

        assert key_1 == key_2

    @pytest.mark.asyncio
    async def test_raw_api_key_is_not_embedded_in_the_storage_key(self):
        limiter_ = RateLimiter(settings=settings_stub(API_KEYS=["super-secret"]))
        key = await limiter_._get_rate_limit_key(make_request(api_key="super-secret"))
        assert "super-secret" not in key

    @pytest.mark.asyncio
    async def test_key_configured_only_in_the_environment_is_recognised(self, monkeypatch):
        """
        Settings and AuthSettings disagree about how to decode API_KEYS, so the
        bucket must not depend on which parser won.
        """
        monkeypatch.setenv("API_KEYS", "env-key-a,env-key-b")
        limiter_ = RateLimiter(settings=settings_stub(API_KEYS=[]))

        key = await limiter_._get_rate_limit_key(make_request(api_key="env-key-a"))

        assert key == build_rate_limit_key(api_key="env-key-a")
        assert key.startswith(API_KEY_KEY_PREFIX)

    @pytest.mark.asyncio
    async def test_key_falls_back_to_ip_without_api_key(self):
        limiter_ = RateLimiter(settings=settings_stub())
        key = await limiter_._get_rate_limit_key(make_request(host="192.168.1.1"))
        assert key == f"{IP_KEY_PREFIX}192.168.1.1"

    @pytest.mark.asyncio
    async def test_unknown_api_key_cannot_mint_a_fresh_bucket(self):
        """Rotating made-up API keys must not escape the IP bucket."""
        limiter_ = RateLimiter(settings=settings_stub(API_KEYS=["known"]))
        key_1 = await limiter_._get_rate_limit_key(make_request(api_key="forged-1", host="10.1.1.1"))
        key_2 = await limiter_._get_rate_limit_key(make_request(api_key="forged-2", host="10.1.1.1"))

        assert key_1 == key_2 == f"{IP_KEY_PREFIX}10.1.1.1"

    @pytest.mark.asyncio
    async def test_forwarded_for_header_is_not_trusted(self):
        limiter_ = RateLimiter(settings=settings_stub())
        request = make_request(host="10.2.2.2")
        request.scope["headers"] = [(b"x-forwarded-for", b"1.2.3.4")]
        key = await limiter_._get_rate_limit_key(request)
        assert key == f"{IP_KEY_PREFIX}10.2.2.2"

    @pytest.mark.asyncio
    async def test_key_unknown_client(self):
        limiter_ = RateLimiter(settings=settings_stub())
        key = await limiter_._get_rate_limit_key(make_request(host=None))
        assert key == f"{IP_KEY_PREFIX}unknown"


class TestApiKeyEnvParsing:
    """Both encodings of API_KEYS must yield the same key set."""

    @pytest.mark.parametrize(
        "raw",
        [
            '["key-a", "key-b"]',  # JSON array (what pydantic-settings decodes)
            "key-a,key-b",  # comma separated (what assemble_api_keys expects)
            " key-a , key-b ",
        ],
    )
    def test_both_encodings_parse_identically(self, monkeypatch, raw):
        monkeypatch.setenv("API_KEYS", raw)
        assert parse_api_keys_env() == {"key-a", "key-b"}

    def test_single_api_key_variable_is_honoured(self, monkeypatch):
        monkeypatch.setenv("API_KEY", "solo-key")
        assert parse_api_keys_env() == {"solo-key"}

    def test_absent_and_blank_yield_nothing(self, monkeypatch):
        assert parse_api_keys_env() == set()
        monkeypatch.setenv("API_KEYS", "   ")
        assert parse_api_keys_env() == set()

    def test_malformed_json_falls_back_to_splitting(self, monkeypatch):
        monkeypatch.setenv("API_KEYS", '["key-a", "key-b"')  # truncated JSON
        assert parse_api_keys_env() == {"key-a", "key-b"}


class TestStateReset:
    """reset_rate_limit_state must clear every global the limiter owns."""

    @pytest.mark.asyncio
    async def test_reset_clears_store_and_cleanup_task(self):
        import app.core.rate_limiter as module

        _rate_limit_store["leftover"] = (5, time.time())
        with patch("app.core.rate_limiter.get_settings", return_value=settings_stub()):
            task = start_cleanup_task(interval=0.01)
        module._redis_manager = object()

        reset_rate_limit_state()

        assert _rate_limit_store == {}
        assert module._cleanup_task is None
        assert module._redis_manager is None
        with contextlib.suppress(asyncio.CancelledError):
            await task
        assert task.cancelled()


# ---------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------


class TestIsRateLimited:
    """Test the counting behaviour of the in-memory backend."""

    @pytest.mark.asyncio
    async def test_disabled(self):
        limiter_ = RateLimiter(settings=settings_stub(RATE_LIMIT_ENABLED=False))
        is_limited, info = await limiter_.is_rate_limited(make_request())
        assert is_limited is False
        assert info == {}

    @pytest.mark.asyncio
    async def test_first_request(self):
        limiter_ = RateLimiter(requests=2, timeframe=60, settings=settings_stub())
        is_limited, info = await limiter_.is_rate_limited(make_request())

        assert is_limited is False
        assert info["current"] == 1
        assert info["limit"] == 2
        assert "headers" in info

    @pytest.mark.asyncio
    async def test_under_and_at_limit(self):
        limiter_ = RateLimiter(requests=3, timeframe=60, settings=settings_stub())
        request = make_request()

        await limiter_.is_rate_limited(request)
        is_limited, info = await limiter_.is_rate_limited(request)
        assert is_limited is False
        assert info["current"] == 2

        is_limited, info = await limiter_.is_rate_limited(request)
        assert is_limited is False, "the Nth request within a limit of N is allowed"
        assert info["current"] == 3

    @pytest.mark.asyncio
    async def test_over_limit(self):
        limiter_ = RateLimiter(requests=2, timeframe=60, settings=settings_stub())
        request = make_request()

        await limiter_.is_rate_limited(request)
        await limiter_.is_rate_limited(request)
        is_limited, info = await limiter_.is_rate_limited(request)

        assert is_limited is True
        assert info["current"] == 3
        assert info["limit"] == 2
        assert info["headers"]["X-RateLimit-Remaining"] == "0"

    @pytest.mark.asyncio
    async def test_two_api_keys_have_independent_budgets(self):
        """This is bug #1: user B must not be limited by user A's traffic."""
        settings = settings_stub(API_KEYS=["key-a", "key-b"])
        limiter_ = RateLimiter(requests=2, timeframe=60, settings=settings)

        for _ in range(3):
            await limiter_.is_rate_limited(make_request(api_key="key-a"))

        limited_a, _ = await limiter_.is_rate_limited(make_request(api_key="key-a"))
        limited_b, info_b = await limiter_.is_rate_limited(make_request(api_key="key-b"))

        assert limited_a is True
        assert limited_b is False
        assert info_b["current"] == 1

    @pytest.mark.asyncio
    async def test_window_expired(self):
        limiter_ = RateLimiter(requests=2, timeframe=1, settings=settings_stub())
        request = make_request()

        await limiter_.is_rate_limited(request)
        await asyncio.sleep(1.1)
        is_limited, info = await limiter_.is_rate_limited(request)

        assert is_limited is False
        assert info["current"] == 1
        assert info["limit"] == 2

    def test_get_rate_limit_headers(self):
        limiter_ = RateLimiter(settings=settings_stub())
        fixed_time = 1000000000.0

        with patch("app.core.rate_limiter.time.time", return_value=fixed_time):
            headers = limiter_._get_rate_limit_headers(5, 10, 3600, fixed_time)

        assert headers["headers"]["X-RateLimit-Limit"] == "10"
        assert headers["headers"]["X-RateLimit-Remaining"] == "5"
        assert headers["headers"]["X-RateLimit-Reset"] == "3600"
        assert headers["current"] == 5
        assert headers["limit"] == 10
        assert headers["reset"] == 3600


# ---------------------------------------------------------------------------
# Responses  (429 + Retry-After)
# ---------------------------------------------------------------------------


class TestLimitResponses:
    """Test the middleware and dependency response paths."""

    @pytest.mark.asyncio
    async def test_limit_disabled_passes_through(self):
        limiter_ = RateLimiter(settings=settings_stub(RATE_LIMIT_ENABLED=False))
        call_next = AsyncMock(return_value="response")
        request = make_request()

        assert await limiter_.limit(request, call_next) == "response"
        call_next.assert_called_once_with(request)

    @pytest.mark.asyncio
    async def test_not_limited_adds_headers(self):
        limiter_ = RateLimiter(requests=5, timeframe=60, settings=settings_stub())
        response = MagicMock()
        response.headers = {}
        call_next = AsyncMock(return_value=response)

        result = await limiter_.limit(make_request(), call_next)

        assert result is response
        assert response.headers["X-RateLimit-Limit"] == "5"
        assert response.headers["X-RateLimit-Remaining"] == "4"
        assert "Retry-After" not in response.headers

    @pytest.mark.asyncio
    async def test_429_response_has_correct_retry_after(self):
        limiter_ = RateLimiter(requests=1, timeframe=60, settings=settings_stub())
        request = make_request()

        await limiter_.limit(request, AsyncMock())
        result = await limiter_.limit(request, AsyncMock())

        assert isinstance(result, JSONResponse)
        assert result.status_code == 429
        retry_after = int(result.headers["retry-after"])
        assert 1 <= retry_after <= 60
        assert retry_after == int(result.headers["x-ratelimit-reset"])
        assert result.headers["x-ratelimit-remaining"] == "0"

    @pytest.mark.asyncio
    async def test_429_dependency_raises_with_retry_after(self):
        limiter_ = RateLimiter(requests=1, timeframe=60, settings=settings_stub())
        request = make_request()

        await limiter_.limit(request)

        with pytest.raises(RateLimitExceededError) as exc_info:
            await limiter_.limit(request)

        exc = exc_info.value
        assert exc.status_code == 429
        assert 1 <= int(exc.headers["Retry-After"]) <= 60
        assert exc.extra["limit"] == 1


# ---------------------------------------------------------------------------
# Failure policy  (bug #5)
# ---------------------------------------------------------------------------


class TestFailurePolicy:
    """A limiter error must fail closed unless the operator opts out."""

    @staticmethod
    def _broken_limiter():
        limiter_ = RateLimiter(requests=5, timeframe=60, settings=settings_stub(REDIS_URL="redis://localhost:6379/0"))
        broken = MagicMock()
        broken.is_available = True
        broken.rate_limit_check = AsyncMock(side_effect=RuntimeError("redis down"))
        return limiter_, broken

    @pytest.mark.asyncio
    async def test_backend_error_is_not_swallowed(self):
        limiter_, broken = self._broken_limiter()
        with patch("app.core.rate_limiter._get_redis_manager", AsyncMock(return_value=broken)):
            with pytest.raises(RateLimiterBackendError):
                await limiter_.is_rate_limited(make_request())

    @pytest.mark.asyncio
    async def test_backend_error_fails_closed_as_middleware(self):
        limiter_, broken = self._broken_limiter()
        call_next = AsyncMock()
        with patch("app.core.rate_limiter._get_redis_manager", AsyncMock(return_value=broken)):
            result = await limiter_.limit(make_request(), call_next)

        assert isinstance(result, JSONResponse)
        assert result.status_code == 503
        assert result.headers["retry-after"] == "1"
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_backend_error_fails_closed_as_dependency(self):
        limiter_, broken = self._broken_limiter()
        with patch("app.core.rate_limiter._get_redis_manager", AsyncMock(return_value=broken)):
            with pytest.raises(ServiceUnavailableError):
                await limiter_.limit(make_request())

    @pytest.mark.asyncio
    async def test_fail_open_requires_explicit_opt_in(self, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_FAIL_OPEN", "true")
        limiter_, broken = self._broken_limiter()
        call_next = AsyncMock(return_value="ok")

        with patch("app.core.rate_limiter._get_redis_manager", AsyncMock(return_value=broken)):
            result = await limiter_.limit(make_request(), call_next)

        assert result == "ok"

    @pytest.mark.asyncio
    async def test_unreadable_settings_fail_closed(self):
        """A limiter that cannot read its config must not pretend to enforce."""
        limiter_ = RateLimiter()  # no override -> resolves settings lazily
        call_next = AsyncMock()

        with patch("app.core.rate_limiter.get_settings", side_effect=RuntimeError("settings exploded")):
            with pytest.raises(RateLimiterConfigurationError):
                _ = limiter_.enabled

            result = await limiter_.limit(make_request(), call_next)

        assert isinstance(result, JSONResponse)
        assert result.status_code == 503
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_redis_silent_fallback_is_treated_as_failure(self):
        """
        RedisManager.rate_limit_check returns ``(True, 0, 0)`` when it cannot
        reach Redis. A request that was never counted must not be reported as
        allowed.
        """
        limiter_ = RateLimiter(requests=5, timeframe=60, settings=settings_stub(REDIS_URL="redis://localhost:6379/0"))
        manager = MagicMock()
        manager.is_available = True
        manager.rate_limit_check = AsyncMock(return_value=(True, 0, 0))

        with patch("app.core.rate_limiter._get_redis_manager", AsyncMock(return_value=manager)):
            with pytest.raises(RateLimiterBackendError):
                await limiter_.is_rate_limited(make_request())

    @pytest.mark.asyncio
    async def test_configured_redis_that_is_down_does_not_fall_back_to_memory(self):
        limiter_ = RateLimiter(requests=1, timeframe=60, settings=settings_stub(REDIS_URL="redis://localhost:6379/0"))
        manager = MagicMock()
        manager.is_available = False

        with patch("app.core.rate_limiter._get_redis_manager", AsyncMock(return_value=manager)):
            with pytest.raises(RateLimiterBackendError):
                await limiter_.is_rate_limited(make_request())

        assert _rate_limit_store == {}, "must not silently switch to per-process counters"


# ---------------------------------------------------------------------------
# Redis backend across simulated workers  (bug #2)
# ---------------------------------------------------------------------------


class TestRedisBackend:
    """The Redis path must share one budget across worker processes."""

    @pytest.mark.asyncio
    async def test_redis_enforces_across_simulated_workers(self):
        manager = fake_redis_manager()
        settings = settings_stub(REDIS_URL="redis://localhost:6379/0", API_KEYS=["shared-key"])

        # Two RateLimiter instances == two worker processes sharing one store.
        worker_1 = RateLimiter(requests=3, timeframe=60, settings=settings)
        worker_2 = RateLimiter(requests=3, timeframe=60, settings=settings)

        results = []
        with patch("app.core.rate_limiter._get_redis_manager", AsyncMock(return_value=manager)):
            for index in range(4):
                worker = worker_1 if index % 2 == 0 else worker_2
                is_limited, _ = await worker.is_rate_limited(make_request(api_key="shared-key"))
                results.append(is_limited)

        assert results == [False, False, False, True], (
            "4 requests against a limit of 3 must be blocked no matter which worker served them"
        )
        assert _rate_limit_store == {}, "Redis path must not touch the in-memory store"

    @pytest.mark.asyncio
    async def test_redis_keys_are_per_api_key(self):
        client = FakeRedisClient()
        manager = fake_redis_manager(client)
        settings = settings_stub(REDIS_URL="redis://localhost:6379/0", API_KEYS=["key-a", "key-b"])
        limiter_ = RateLimiter(requests=1, timeframe=60, settings=settings)

        with patch("app.core.rate_limiter._get_redis_manager", AsyncMock(return_value=manager)):
            await limiter_.is_rate_limited(make_request(api_key="key-a"))
            limited_b, _ = await limiter_.is_rate_limited(make_request(api_key="key-b"))

        assert limited_b is False
        assert len(client.counters) == 2
        assert all(k.startswith(API_KEY_KEY_PREFIX) for k in client.counters)

    @pytest.mark.asyncio
    async def test_redis_window_expiry_resets_the_budget(self):
        client = FakeRedisClient()
        manager = fake_redis_manager(client)
        settings = settings_stub(REDIS_URL="redis://localhost:6379/0")
        limiter_ = RateLimiter(requests=1, timeframe=60, settings=settings)

        with patch("app.core.rate_limiter._get_redis_manager", AsyncMock(return_value=manager)):
            await limiter_.is_rate_limited(make_request())
            limited, _ = await limiter_.is_rate_limited(make_request())
            assert limited is True

            client.now += 61  # window elapsed
            limited, info = await limiter_.is_rate_limited(make_request())

        assert limited is False
        assert info["current"] == 1


# ---------------------------------------------------------------------------
# Deployment validation  (bug #2)
# ---------------------------------------------------------------------------


class TestDeploymentValidation:
    """Refuse to run multi-worker in production without a shared store."""

    def test_worker_count_defaults_to_one(self):
        assert get_worker_count() == 1

    def test_worker_count_reads_env(self, monkeypatch):
        monkeypatch.setenv("WEB_CONCURRENCY", "4")
        assert get_worker_count() == 4

    def test_worker_count_ignores_garbage(self, monkeypatch):
        monkeypatch.setenv("WEB_CONCURRENCY", "not-a-number")
        assert get_worker_count() == 1

    @pytest.mark.parametrize(
        "argv",
        [
            ["uvicorn", "main:app", "--workers", "4"],
            ["uvicorn", "main:app", "--workers=4"],
            ["gunicorn", "-w", "4", "main:app"],
        ],
    )
    def test_worker_count_reads_the_command_line(self, monkeypatch, argv):
        """uvicorn/gunicorn --workers sets no environment variable."""
        monkeypatch.setattr("sys.argv", argv)
        assert get_worker_count() == 4

    def test_command_line_workers_trigger_the_startup_refusal(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["uvicorn", "main:app", "--workers", "4"])
        settings = settings_stub(ENVIRONMENT="production", REDIS_URL=None)

        assert requires_shared_store(settings) is True
        with pytest.raises(RateLimiterConfigurationError):
            validate_rate_limit_configuration(settings)

    def test_production_multi_worker_without_redis_is_refused(self, monkeypatch):
        monkeypatch.setenv("WEB_CONCURRENCY", "4")
        settings = settings_stub(ENVIRONMENT="production", REDIS_URL=None)

        assert requires_shared_store(settings) is True
        with pytest.raises(RateLimiterConfigurationError) as exc_info:
            validate_rate_limit_configuration(settings)
        assert "REDIS_URL" in str(exc_info.value)

    def test_production_multi_worker_with_redis_is_allowed(self, monkeypatch):
        monkeypatch.setenv("WEB_CONCURRENCY", "4")
        settings = settings_stub(ENVIRONMENT="production", REDIS_URL="redis://localhost:6379/0")

        assert requires_shared_store(settings) is False
        validate_rate_limit_configuration(settings)  # must not raise

    def test_single_worker_without_redis_is_allowed(self, monkeypatch):
        monkeypatch.setenv("WEB_CONCURRENCY", "1")
        settings = settings_stub(ENVIRONMENT="production")

        assert requires_shared_store(settings) is False
        validate_rate_limit_configuration(settings)  # must not raise

    def test_disabled_rate_limiting_is_allowed(self, monkeypatch):
        monkeypatch.setenv("WEB_CONCURRENCY", "4")
        settings = settings_stub(ENVIRONMENT="production", RATE_LIMIT_ENABLED=False)

        assert requires_shared_store(settings) is False
        validate_rate_limit_configuration(settings)  # must not raise

    @pytest.mark.asyncio
    async def test_startup_refuses_the_unsafe_deployment(self, monkeypatch):
        """start_cleanup_task is the startup hook: it must refuse, not warn."""
        monkeypatch.setenv("WEB_CONCURRENCY", "4")
        settings = settings_stub(ENVIRONMENT="production", REDIS_URL=None)

        with patch("app.core.rate_limiter.get_settings", return_value=settings):
            with pytest.raises(RateLimiterConfigurationError):
                start_cleanup_task()

        await shutdown_rate_limiting()

    @pytest.mark.asyncio
    async def test_requests_are_rejected_if_startup_validation_was_bypassed(self, monkeypatch):
        monkeypatch.setenv("WEB_CONCURRENCY", "4")
        settings = settings_stub(ENVIRONMENT="production", REDIS_URL=None)
        limiter_ = RateLimiter(requests=5, timeframe=60, settings=settings)

        with pytest.raises(RateLimiterConfigurationError):
            await limiter_.is_rate_limited(make_request())


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class TestRateLimitMiddleware:
    """Test cases for RateLimitMiddleware class."""

    def test_init(self):
        middleware = RateLimitMiddleware(MagicMock(), requests=50, timeframe=1800)
        assert middleware.limiter.requests == 50
        assert middleware.limiter.timeframe == 1800

    @pytest.mark.asyncio
    async def test_dispatch_not_limited(self):
        middleware = RateLimitMiddleware(MagicMock(), requests=5, timeframe=60, settings=settings_stub())
        response = MagicMock()
        response.headers = {}
        result = await middleware.dispatch(make_request(), AsyncMock(return_value=response))
        assert result is response

    @pytest.mark.asyncio
    async def test_dispatch_rate_limited(self):
        middleware = RateLimitMiddleware(MagicMock(), requests=1, timeframe=60, settings=settings_stub())
        request = make_request()

        await middleware.dispatch(request, AsyncMock(return_value=MagicMock(headers={})))
        result = await middleware.dispatch(request, AsyncMock())

        assert isinstance(result, JSONResponse)
        assert result.status_code == 429


# ---------------------------------------------------------------------------
# Module-level dependency
# ---------------------------------------------------------------------------


class TestGlobalFunctions:
    """Test cases for global functions and dependencies."""

    @pytest.mark.asyncio
    async def test_rate_limit_dependency_disabled(self):
        import app.core.rate_limiter as module

        original = module.limiter
        module.limiter = RateLimiter(settings=settings_stub(RATE_LIMIT_ENABLED=False))
        try:
            await rate_limit(make_request())
        finally:
            module.limiter = original

    @pytest.mark.asyncio
    async def test_rate_limit_dependency_raises_when_limited(self):
        import app.core.rate_limiter as module

        original = module.limiter
        module.limiter = RateLimiter(requests=1, timeframe=60, settings=settings_stub())
        try:
            request = make_request()
            await rate_limit(request)
            with pytest.raises(RateLimitExceededError):
                await rate_limit(request)
        finally:
            module.limiter = original

    def test_global_limiter_is_importable(self):
        assert isinstance(limiter, RateLimiter)


# ---------------------------------------------------------------------------
# Cleanup task  (bug #3)
# ---------------------------------------------------------------------------


class TestCleanupTask:
    """The cleanup task must actually purge, and must be cancellable."""

    @pytest.mark.asyncio
    async def test_purge_removes_only_expired_entries(self):
        now = time.time()
        _rate_limit_store["fresh"] = (1, now)
        _rate_limit_store["stale"] = (1, now - 120)

        removed = await purge_expired_entries(now=now, timeframe=60)

        assert removed == 1
        assert "fresh" in _rate_limit_store
        assert "stale" not in _rate_limit_store

    @pytest.mark.asyncio
    async def test_cleanup_task_purges_the_store(self):
        settings = settings_stub(RATE_LIMIT_TIMEFRAME=1)
        _rate_limit_store["stale"] = (1, time.time() - 120)

        with patch("app.core.rate_limiter.get_settings", return_value=settings):
            task = asyncio.create_task(cleanup_rate_limit_store(interval=0.01))
            try:
                for _ in range(100):
                    await asyncio.sleep(0.01)
                    if not _rate_limit_store:
                        break
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

        assert _rate_limit_store == {}, "cleanup task did not remove expired entries"

    @pytest.mark.asyncio
    async def test_cleanup_task_survives_backend_errors(self):
        calls = []

        async def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("boom")
            return 0

        with patch("app.core.rate_limiter.purge_expired_entries", flaky):
            task = asyncio.create_task(cleanup_rate_limit_store(interval=0.01))
            for _ in range(100):
                await asyncio.sleep(0.01)
                if len(calls) >= 3:
                    break
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert len(calls) >= 3, "loop must keep running after an error"

    @pytest.mark.asyncio
    async def test_start_and_stop_cleanup_task(self):
        settings = settings_stub()
        with patch("app.core.rate_limiter.get_settings", return_value=settings):
            task = start_cleanup_task(interval=0.01)
            assert isinstance(task, asyncio.Task)
            assert not task.done()

            # Idempotent while running.
            assert start_cleanup_task(interval=0.01) is task

            stop_cleanup_task()
            await shutdown_rate_limiting()

        assert task.cancelled() or task.done()

    @pytest.mark.asyncio
    async def test_shutdown_is_safe_without_a_running_task(self):
        await shutdown_rate_limiting()
        await shutdown_rate_limiting()

    @pytest.mark.asyncio
    async def test_start_cleanup_task_restarts_after_shutdown(self):
        settings = settings_stub()
        with patch("app.core.rate_limiter.get_settings", return_value=settings):
            first = start_cleanup_task(interval=0.01)
            await shutdown_rate_limiting()
            second = start_cleanup_task(interval=0.01)
            assert second is not first
            await shutdown_rate_limiting()


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class TestRateLimitStore:
    """Test cases for the in-memory rate limit store."""

    @pytest.mark.asyncio
    async def test_store_persistence(self):
        limiter_ = RateLimiter(requests=3, timeframe=60, settings=settings_stub())
        request = make_request()

        await limiter_.is_rate_limited(request)
        assert len(_rate_limit_store) == 1
        key = next(iter(_rate_limit_store))
        assert _rate_limit_store[key][0] == 1

        await limiter_.is_rate_limited(request)
        assert _rate_limit_store[key][0] == 2

    @pytest.mark.asyncio
    async def test_store_cleanup_via_task_helper(self):
        limiter_ = RateLimiter(requests=2, timeframe=1, settings=settings_stub())

        await limiter_.is_rate_limited(make_request())
        assert len(_rate_limit_store) == 1

        await asyncio.sleep(1.1)
        removed = await purge_expired_entries(timeframe=1)

        assert removed == 1
        assert len(_rate_limit_store) == 0
