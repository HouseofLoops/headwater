"""
Integration tests for rate limiting.

These tests verify that rate limiting works correctly across the application.

The application object is built by ``main.create_application()`` and the rate
limit middleware is attached here, because ``main.py`` does not yet install it
(see the CRT-8 report: ``main.py`` should call
``app.add_middleware(RateLimitMiddleware)`` at startup). Attaching the real
``RateLimitMiddleware`` means these tests exercise the production limiter code
end to end -- key derivation, counting, 429 rendering and headers.
"""

import os

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.rate_limiter import RateLimitMiddleware, reset_rate_limit_state

ISOLATED_ENV_VARS = (
    "WEB_CONCURRENCY",
    "UVICORN_WORKERS",
    "GUNICORN_WORKERS",
    "WORKERS",
    "RATE_LIMIT_FAIL_OPEN",
    "REDIS_URL",
    "API_KEY",
)


@pytest.fixture(autouse=True)
def _isolate_rate_limiter():
    """
    Guarantee isolation by construction, not by test ordering.

    The limiter's store, cleanup task handle and cached Redis manager are
    process globals shared with tests/test_rate_limiter.py, so they are reset
    around every test here rather than relying on each test remembering to
    clear the store itself.
    """
    saved = {var: os.environ.pop(var, None) for var in ISOLATED_ENV_VARS}
    reset_rate_limit_state()
    get_settings.cache_clear()
    yield
    reset_rate_limit_state()
    for var, value in saved.items():
        if value is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = value
    get_settings.cache_clear()


def _install_rate_limiting(app):
    """
    Attach the real rate limit middleware to a freshly created app.

    Idempotent: main.py is expected to grow an
    ``app.add_middleware(RateLimitMiddleware)`` call of its own, and installing
    a second copy would count every request twice.
    """
    already_installed = any(
        getattr(middleware, "cls", None) is RateLimitMiddleware for middleware in getattr(app, "user_middleware", [])
    )
    if not already_installed:
        app.add_middleware(RateLimitMiddleware)
    return app


@pytest.fixture
def rate_limited_app():
    """Create app with rate limiting enabled."""
    # Store original values
    original_rate = os.environ.get("RATE_LIMIT_ENABLED")
    original_requests = os.environ.get("RATE_LIMIT_REQUESTS")
    original_timeframe = os.environ.get("RATE_LIMIT_TIMEFRAME")
    original_auth = os.environ.get("ENABLE_API_KEY_AUTH")
    original_keys = os.environ.get("API_KEYS")

    # Set test configuration - low limits for testing
    os.environ["RATE_LIMIT_ENABLED"] = "true"
    os.environ["RATE_LIMIT_REQUESTS"] = "5"  # Only 5 requests allowed
    os.environ["RATE_LIMIT_TIMEFRAME"] = "60"  # Per minute
    os.environ["ENABLE_API_KEY_AUTH"] = "false"

    # Clear cached settings
    from app.core.config import get_settings

    get_settings.cache_clear()

    # Clear rate limit store
    from app.core.rate_limiter import _rate_limit_store

    _rate_limit_store.clear()

    from main import create_application

    app = _install_rate_limiting(create_application())

    yield app

    # Restore original values
    if original_rate:
        os.environ["RATE_LIMIT_ENABLED"] = original_rate
    else:
        os.environ.pop("RATE_LIMIT_ENABLED", None)

    if original_requests:
        os.environ["RATE_LIMIT_REQUESTS"] = original_requests
    else:
        os.environ.pop("RATE_LIMIT_REQUESTS", None)

    if original_timeframe:
        os.environ["RATE_LIMIT_TIMEFRAME"] = original_timeframe
    else:
        os.environ.pop("RATE_LIMIT_TIMEFRAME", None)

    if original_auth:
        os.environ["ENABLE_API_KEY_AUTH"] = original_auth
    else:
        os.environ.pop("ENABLE_API_KEY_AUTH", None)

    if original_keys:
        os.environ["API_KEYS"] = original_keys
    else:
        os.environ.pop("API_KEYS", None)

    # Clear settings cache
    get_settings.cache_clear()

    # Clear rate limit store
    _rate_limit_store.clear()


@pytest.fixture
def rate_client(rate_limited_app):
    """Create test client for rate-limited app."""
    return TestClient(rate_limited_app)


class TestRateLimitHeaders:
    """Test rate limit response headers."""

    def test_rate_limit_headers_present(self, rate_client):
        """Test that rate limit headers are present in responses."""
        # Clear rate limit store before test
        from app.core.rate_limiter import _rate_limit_store

        _rate_limit_store.clear()

        response = rate_client.get("/api-config")

        assert response.status_code == 200
        headers = response.headers
        assert headers["x-ratelimit-limit"] == "5"
        assert headers["x-ratelimit-remaining"] == "4"
        assert int(headers["x-ratelimit-reset"]) <= 60

    def test_rate_limit_remaining_decreases(self, rate_client):
        """Test that remaining count decreases with each request."""
        from app.core.rate_limiter import _rate_limit_store

        _rate_limit_store.clear()

        remaining1 = rate_client.get("/api-config").headers["x-ratelimit-remaining"]
        remaining2 = rate_client.get("/api-config").headers["x-ratelimit-remaining"]

        assert int(remaining2) < int(remaining1)


class TestRateLimitEnforcement:
    """Test that rate limits are enforced."""

    def test_rate_limit_exceeded(self, rate_client):
        """Test that exceeding rate limit returns 429."""
        from app.core.rate_limiter import _rate_limit_store

        _rate_limit_store.clear()

        # Make requests up to the limit (5) plus one more
        responses = []
        for _ in range(7):
            response = rate_client.get("/api-config")
            responses.append(response.status_code)

        # At least one should be 429 (Too Many Requests)
        assert 429 in responses, f"Expected 429 in responses: {responses}"
        # And enforcement must start exactly at the configured limit.
        assert responses == [200] * 5 + [429] * 2, responses

    def test_rate_limit_429_response_format(self, rate_client):
        """Test that 429 response has correct format."""
        from app.core.rate_limiter import _rate_limit_store

        _rate_limit_store.clear()

        limited = None
        for _ in range(10):
            response = rate_client.get("/api-config")
            if response.status_code == 429:
                limited = response
                break

        assert limited is not None, "rate limit was never enforced"
        data = limited.json()
        assert "detail" in data or "title" in data or "message" in data
        assert data["status"] == 429
        assert data["limit"] == 5

    def test_rate_limit_429_has_retry_after(self, rate_client):
        """A 429 must tell the client when to come back."""
        from app.core.rate_limiter import _rate_limit_store

        _rate_limit_store.clear()

        limited = None
        for _ in range(10):
            response = rate_client.get("/api-config")
            if response.status_code == 429:
                limited = response
                break

        assert limited is not None, "rate limit was never enforced"
        retry_after = int(limited.headers["retry-after"])
        assert 1 <= retry_after <= 60
        assert retry_after == int(limited.headers["x-ratelimit-reset"])
        assert limited.headers["x-ratelimit-remaining"] == "0"

    def test_limit_is_per_bucket_not_global(self, rate_client):
        """Exhausting one bucket must not be a shortcut to a global lockout."""
        from app.core.rate_limiter import _rate_limit_store

        _rate_limit_store.clear()

        for _ in range(7):
            rate_client.get("/api-config")

        # Same client, so still limited...
        assert rate_client.get("/api-config").status_code == 429

        # ...but a different bucket is unaffected.
        _rate_limit_store.clear()
        assert rate_client.get("/api-config").status_code == 200


class TestRateLimitByKey:
    """Test rate limiting by API key."""

    @pytest.fixture
    def rate_auth_app(self):
        """Create app with both rate limiting and auth enabled."""
        os.environ["RATE_LIMIT_ENABLED"] = "true"
        os.environ["RATE_LIMIT_REQUESTS"] = "3"
        os.environ["RATE_LIMIT_TIMEFRAME"] = "60"
        os.environ["ENABLE_API_KEY_AUTH"] = "true"
        # NOTE: the JSON-array form is used deliberately. pydantic-settings
        # JSON-decodes list fields straight from the environment, *before*
        # Settings.assemble_api_keys runs, so the documented comma-separated
        # form ("a,b") makes Settings() raise SettingsError. That is a bug in
        # app/core/config.py (and app/core/auth.py), not in the limiter --
        # reported separately; this fixture avoids tripping it.
        os.environ["API_KEYS"] = '["key-user-a", "key-user-b"]'

        from app.core.config import get_settings

        get_settings.cache_clear()

        from app.core.rate_limiter import _rate_limit_store

        _rate_limit_store.clear()

        from main import create_application

        app = _install_rate_limiting(create_application())

        yield app

        os.environ.pop("RATE_LIMIT_ENABLED", None)
        os.environ.pop("RATE_LIMIT_REQUESTS", None)
        os.environ.pop("RATE_LIMIT_TIMEFRAME", None)
        os.environ.pop("ENABLE_API_KEY_AUTH", None)
        os.environ.pop("API_KEYS", None)
        get_settings.cache_clear()
        _rate_limit_store.clear()

    def test_separate_limits_per_api_key(self, rate_auth_app):
        """Test that different API keys have separate rate limits."""
        from app.core.rate_limiter import _rate_limit_store

        _rate_limit_store.clear()

        client = TestClient(rate_auth_app)

        # User A makes requests
        for _ in range(3):
            client.get("/api-config", headers={"X-API-Key": "key-user-a"})

        # Precondition, so a regression here reports its cause rather than just
        # "B got 429": A's traffic must have landed in a per-API-key bucket. If
        # the configured keys stop being recognised, both users collapse onto
        # one IP bucket and this test fails for the CRT-8 reason all over again.
        assert len(_rate_limit_store) == 1, _rate_limit_store
        assert next(iter(_rate_limit_store)).startswith("rate_limit:api_key:"), (
            f"API key not recognised; both users would share the IP bucket: {list(_rate_limit_store)}"
        )

        # User A should be rate limited
        response_a = client.get("/api-config", headers={"X-API-Key": "key-user-a"})

        # User B should still be able to make requests
        response_b = client.get("/api-config", headers={"X-API-Key": "key-user-b"})

        # User A has spent its budget...
        assert response_a.status_code == 429
        # ...and User B should succeed (not rate limited)
        assert response_b.status_code == 200

    def test_bucket_key_is_the_api_key_not_the_ip(self, rate_auth_app):
        """
        The CRT-8 regression: keys of the form ``rate_limit:ip:`` proved that
        API-key bucketing never worked.
        """
        from app.core.rate_limiter import _rate_limit_store

        _rate_limit_store.clear()

        client = TestClient(rate_auth_app)
        client.get("/api-config", headers={"X-API-Key": "key-user-a"})

        assert len(_rate_limit_store) == 1
        key = next(iter(_rate_limit_store))
        assert key.startswith("rate_limit:api_key:"), key
        assert not key.startswith("rate_limit:ip:"), key

    def test_two_keys_share_one_ip_but_not_one_budget(self, rate_auth_app):
        """Both users come from the same test client IP; budgets must differ."""
        from app.core.rate_limiter import _rate_limit_store

        _rate_limit_store.clear()

        client = TestClient(rate_auth_app)
        for _ in range(4):
            client.get("/api-config", headers={"X-API-Key": "key-user-a"})
        for _ in range(4):
            client.get("/api-config", headers={"X-API-Key": "key-user-b"})

        assert len(_rate_limit_store) == 2, _rate_limit_store
        assert all(k.startswith("rate_limit:api_key:") for k in _rate_limit_store)

    def test_unknown_api_key_cannot_buy_a_fresh_budget(self, rate_auth_app):
        """Made-up keys must fall back to the shared IP bucket."""
        from app.core.rate_limiter import _rate_limit_store

        _rate_limit_store.clear()

        client = TestClient(rate_auth_app)
        statuses = [client.get("/api-config", headers={"X-API-Key": f"forged-{i}"}).status_code for i in range(6)]

        assert 429 in statuses, statuses
        assert all(k.startswith("rate_limit:ip:") for k in _rate_limit_store)


class TestRateLimitDisabled:
    """Test behavior when rate limiting is disabled."""

    @pytest.fixture
    def no_rate_app(self):
        """Create app with rate limiting disabled."""
        os.environ["RATE_LIMIT_ENABLED"] = "false"
        os.environ["ENABLE_API_KEY_AUTH"] = "false"

        from app.core.config import get_settings

        get_settings.cache_clear()

        from main import create_application

        app = _install_rate_limiting(create_application())

        yield app

        os.environ.pop("RATE_LIMIT_ENABLED", None)
        os.environ.pop("ENABLE_API_KEY_AUTH", None)
        get_settings.cache_clear()

    def test_no_rate_limit_when_disabled(self, no_rate_app):
        """Test that rate limiting doesn't apply when disabled."""
        client = TestClient(no_rate_app)

        # Make many requests
        responses = []
        for _ in range(20):
            response = client.get("/api-config")
            responses.append(response.status_code)

        # None should be 429
        assert 429 not in responses, "Rate limiting should be disabled"
