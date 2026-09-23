"""
Integration tests for authentication flow.

These tests verify the API key authentication works correctly
across the application.
"""

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def auth_enabled_app():
    """Create app with authentication enabled."""
    # Store original values
    original_keys = os.environ.get("API_KEYS")
    original_auth = os.environ.get("ENABLE_API_KEY_AUTH")
    original_rate = os.environ.get("RATE_LIMIT_ENABLED")

    # Set test configuration
    os.environ["API_KEYS"] = "valid-test-key-123,another-valid-key-456"
    os.environ["ENABLE_API_KEY_AUTH"] = "true"
    os.environ["RATE_LIMIT_ENABLED"] = "false"

    # Clear any cached settings
    from app.core.config import get_settings

    get_settings.cache_clear()

    from main import create_application

    app = create_application()

    yield app

    # Restore original values
    if original_keys:
        os.environ["API_KEYS"] = original_keys
    else:
        os.environ.pop("API_KEYS", None)

    if original_auth:
        os.environ["ENABLE_API_KEY_AUTH"] = original_auth
    else:
        os.environ.pop("ENABLE_API_KEY_AUTH", None)

    if original_rate:
        os.environ["RATE_LIMIT_ENABLED"] = original_rate
    else:
        os.environ.pop("RATE_LIMIT_ENABLED", None)

    # Clear settings cache
    get_settings.cache_clear()


PROTECTED_PROBE = "/__auth_probe__"


def _attach_probe(app):
    """
    Register a route guarded by the real auth dependency.

    Several tests below asserted 401 against ``/api-config``, which is a
    PUBLIC endpoint (main.py registers it with no dependency), so they could
    never pass. Point them at a route that really is protected instead, and
    keep them off the network at the same time.
    """
    from fastapi import Depends

    from app.core.auth import get_api_key

    @app.get(PROTECTED_PROBE)
    async def _probe(api_key: str = Depends(get_api_key)):
        return {"api_key": api_key}

    return app


@pytest.fixture
def auth_client(auth_enabled_app):
    """Create test client for auth-enabled app."""
    return TestClient(_attach_probe(auth_enabled_app))


class TestApiKeyAuthentication:
    """Integration tests for API key authentication."""

    def test_public_endpoints_without_auth(self, auth_client):
        """Liveness probes stay public; nothing else does.

        /health and /ping exist for load balancers and container health
        checks, which cannot present a key. /health is liveness ONLY -- it
        used to return version and environment, which is why /status below is
        no longer in this list.
        """
        response = auth_client.get("/health")
        assert response.status_code == 200

        response = auth_client.get("/ping")
        assert response.status_code == 200

    def test_status_requires_auth(self, auth_client):
        """/status discloses the running version and environment.

        This test previously asserted /status was public. That was the bug,
        not the contract: an unauthenticated caller could read the deployed
        version and environment, which is a free reconnaissance step toward
        matching a deployment against known CVEs. It is now key-gated.
        """
        response = auth_client.get("/status")
        assert response.status_code == 401

        response = auth_client.get("/status", headers={"X-API-Key": "valid-test-key-123"})
        assert response.status_code == 200, response.text

    def test_docs_endpoints_without_auth(self, auth_client):
        """Test that documentation endpoints don't require authentication."""
        response = auth_client.get("/api/docs")
        assert response.status_code == 200

        response = auth_client.get("/api/redoc")
        assert response.status_code == 200

        response = auth_client.get("/openapi.json")
        assert response.status_code == 200

    def test_valid_api_key_in_header(self, auth_client):
        """Test that valid API key in header is accepted."""
        response = auth_client.get(PROTECTED_PROBE, headers={"X-API-Key": "valid-test-key-123"})
        assert response.status_code == 200, response.text

    def test_another_valid_api_key(self, auth_client):
        """Test that another valid API key is accepted."""
        response = auth_client.get(PROTECTED_PROBE, headers={"X-API-Key": "another-valid-key-456"})
        assert response.status_code == 200, response.text

    def test_invalid_api_key_rejected(self, auth_client):
        """Test that invalid API key is rejected on a protected route."""
        response = auth_client.get(PROTECTED_PROBE, headers={"X-API-Key": "invalid-key"})
        # Should return 401 or 403
        assert response.status_code in [401, 403], response.text

    def test_valid_api_key_on_protected_route(self, auth_client):
        """A configured key must actually authenticate, not 500."""
        response = auth_client.get(PROTECTED_PROBE, headers={"X-API-Key": "valid-test-key-123"})
        assert response.status_code == 200, response.text
        assert response.json()["api_key"] == "valid-test-key-123"

    def test_missing_api_key_rejected_on_protected_route(self, auth_client):
        """No header on a protected route must be 401, not 500 and not 200."""
        response = auth_client.get(PROTECTED_PROBE)
        assert response.status_code == 401, response.text

    def test_missing_api_key_rejected(self, auth_client):
        """Test that missing API key is rejected for protected endpoints."""
        # Note: /api-config might be public, test an actual API endpoint
        response = auth_client.get("/api/v1/google-news/search?query=test")
        # Should return 401 or 403
        assert response.status_code in [401, 403, 422]

    def test_empty_api_key_rejected(self, auth_client):
        """Test that empty API key is rejected."""
        response = auth_client.get("/api/v1/google-news/search?query=test", headers={"X-API-Key": ""})
        assert response.status_code in [401, 403, 422]

    def test_case_sensitive_header_name(self, auth_client):
        """Test that header name is handled correctly."""
        # Standard header name
        response = auth_client.get(PROTECTED_PROBE, headers={"X-API-Key": "valid-test-key-123"})
        assert response.status_code == 200, response.text

    def test_whitespace_in_api_key_handled(self, auth_client):
        """Test that whitespace around API key is handled."""
        response = auth_client.get(PROTECTED_PROBE, headers={"X-API-Key": " valid-test-key-123 "})
        # Depends on implementation - might strip whitespace or reject
        # Just verify it doesn't crash
        assert response.status_code in [200, 401, 403]


class TestAuthenticationDisabled:
    """Test behavior when authentication is disabled."""

    @pytest.fixture
    def no_auth_app(self):
        """Create app with authentication disabled."""
        original_auth = os.environ.get("ENABLE_API_KEY_AUTH")
        os.environ["ENABLE_API_KEY_AUTH"] = "false"

        from app.core.config import get_settings

        get_settings.cache_clear()

        from main import create_application

        app = create_application()

        yield app

        if original_auth:
            os.environ["ENABLE_API_KEY_AUTH"] = original_auth
        else:
            os.environ.pop("ENABLE_API_KEY_AUTH", None)

        get_settings.cache_clear()

    def test_api_accessible_without_key(self, no_auth_app):
        """Test that API is accessible without key when auth is disabled."""
        client = TestClient(no_auth_app)
        response = client.get("/api-config")
        assert response.status_code == 200

    def test_protected_route_accessible_without_header(self, no_auth_app):
        """
        With auth disabled, a protected route must accept a request that
        sends NO X-API-Key header at all.

        APIKeyHeader(auto_error=True) used to reject the header-less request
        inside FastAPI before the "auth disabled" branch could run, so
        disabling auth inverted it: header-less probes looked locked while
        `X-API-Key: anything` returned 200 with real data.
        """
        client = TestClient(_attach_probe(no_auth_app))
        response = client.get(PROTECTED_PROBE)
        assert response.status_code == 200, response.text
        assert response.json()["api_key"] == "authentication-disabled"


class TestAuthErrorResponses:
    """Test authentication error response formats."""

    def test_unauthorized_response_format(self, auth_client):
        """Test that unauthorized response follows expected format."""
        response = auth_client.get("/api/v1/google-news/search?query=test", headers={"X-API-Key": "invalid-key"})

        if response.status_code in [401, 403]:
            data = response.json()
            # Should have error information
            assert "detail" in data or "message" in data or "title" in data
