"""
Integration tests for API endpoints.

These tests verify the API endpoints work correctly with the full
application stack, using minimal mocking.
"""
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app():
    """Create a test application instance."""
    # Set test environment variables
    os.environ.setdefault("ENVIRONMENT", "testing")
    os.environ.setdefault("DEBUG", "false")
    os.environ.setdefault("ENABLE_API_KEY_AUTH", "false")
    os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

    # The env vars above are only honoured if the settings cache is dropped;
    # otherwise get_settings() keeps whatever was resolved at first import and
    # the fixture silently tests a different configuration than it declares.
    from app.core.config import get_settings
    get_settings.cache_clear()

    from main import create_application
    return create_application()


@pytest.fixture
def client(app):
    """Create a test client."""
    return TestClient(app)


@pytest.fixture
def authenticated_client(app):
    """Create a test client with API key authentication."""
    os.environ["API_KEYS"] = "test-api-key-123"
    os.environ["ENABLE_API_KEY_AUTH"] = "true"

    from main import create_application
    app = create_application()
    client = TestClient(app)
    client.headers["X-API-Key"] = "test-api-key-123"

    yield client

    # Cleanup
    os.environ.pop("API_KEYS", None)
    os.environ["ENABLE_API_KEY_AUTH"] = "false"


class TestHealthEndpoints:
    """Integration tests for health check endpoints."""

    def test_health_endpoint_returns_200(self, client):
        """Test that /health endpoint returns 200 OK."""
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_endpoint_response_structure(self, client):
        """
        /health is the unauthenticated liveness probe and must disclose
        nothing beyond liveness.

        This test previously asserted that version and environment WERE
        present. That is the information-disclosure bug, not the contract:
        an unauthenticated caller learning the exact running version learns
        which CVEs to try. Build details now live on the authenticated
        /status endpoint.
        """
        response = client.get("/health")
        data = response.json()

        assert data == {"status": "healthy"}
        assert "version" not in data
        assert "environment" not in data

    def test_ping_endpoint(self, client):
        """Test /ping endpoint returns pong."""
        response = client.get("/ping")
        assert response.status_code == 200
        assert response.json() == {"ping": "pong"}

    def test_status_endpoint(self, client):
        """Test /status endpoint returns online status."""
        response = client.get("/status")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "online"
        assert "version" in data
        assert "uptime" in data

    def test_detailed_health_endpoint(self, client):
        """Test /health/detailed endpoint returns component status."""
        response = client.get("/health/detailed")
        assert response.status_code == 200

        data = response.json()
        assert "status" in data


class TestApiConfiguration:
    """Integration tests for API configuration endpoints."""

    def test_api_config_endpoint(self, client):
        """Test /api-config endpoint returns configuration."""
        response = client.get("/api-config")
        assert response.status_code == 200

        data = response.json()
        assert "name" in data
        assert "version" in data
        assert "rate_limiting" in data
        assert "caching" in data
        assert "cors" in data

    def test_config_sources_endpoint(self, client):
        """Test /config-sources endpoint."""
        response = client.get("/config-sources")
        assert response.status_code == 200

        data = response.json()
        assert "environment_variables" in data
        assert "defaults" in data


class TestDocumentationEndpoints:
    """Integration tests for documentation endpoints."""

    def test_swagger_docs_available(self, client):
        """Test Swagger documentation is accessible."""
        response = client.get("/api/docs")
        assert response.status_code == 200
        assert "swagger" in response.text.lower() or "openapi" in response.text.lower()

    def test_redoc_available(self, client):
        """Test ReDoc documentation is accessible."""
        response = client.get("/api/redoc")
        assert response.status_code == 200
        assert "redoc" in response.text.lower()

    def test_openapi_schema_available(self, client):
        """Test OpenAPI schema is accessible."""
        response = client.get("/openapi.json")
        assert response.status_code == 200

        data = response.json()
        assert "openapi" in data
        assert "info" in data
        assert "paths" in data


class TestApiRouterStructure:
    """Integration tests for API router structure."""

    def test_google_news_router_exists(self, client):
        """Test Google News API router is registered."""
        response = client.get("/openapi.json")
        data = response.json()

        paths = data.get("paths", {})
        news_paths = [p for p in paths.keys() if "google-news" in p]
        assert len(news_paths) > 0, "Google News API routes not found"

    def test_google_trends_router_exists(self, client):
        """Test Google Trends API router is registered."""
        response = client.get("/openapi.json")
        data = response.json()

        paths = data.get("paths", {})
        trends_paths = [p for p in paths.keys() if "google-trends" in p]
        assert len(trends_paths) > 0, "Google Trends API routes not found"

    def test_google_autocomplete_router_exists(self, client):
        """Test Google Autocomplete API router is registered."""
        response = client.get("/openapi.json")
        data = response.json()

        paths = data.get("paths", {})
        autocomplete_paths = [p for p in paths.keys() if "google-autocomplete" in p]
        assert len(autocomplete_paths) > 0, "Google Autocomplete API routes not found"

    def test_youtube_transcripts_router_exists(self, client):
        """Test YouTube Transcripts API router is registered."""
        response = client.get("/openapi.json")
        data = response.json()

        paths = data.get("paths", {})
        youtube_paths = [p for p in paths.keys() if "youtube-transcript" in p]
        assert len(youtube_paths) > 0, "YouTube Transcripts API routes not found"

    def test_api_versioning(self, client):
        """Test that API routes use v1 versioning."""
        response = client.get("/openapi.json")
        data = response.json()

        paths = data.get("paths", {})
        api_paths = [p for p in paths.keys() if p.startswith("/api/")]

        for path in api_paths:
            assert "/v1/" in path, f"API path {path} should use v1 versioning"


class TestErrorHandling:
    """Integration tests for error handling."""

    def test_404_for_unknown_endpoint(self, client):
        """Test 404 response for unknown endpoints."""
        response = client.get("/unknown/endpoint/that/does/not/exist")
        assert response.status_code == 404

    def test_method_not_allowed(self, client):
        """Test 405 response for wrong HTTP method."""
        # POST to a GET-only endpoint
        response = client.post("/health")
        assert response.status_code == 405


class TestCorsHeaders:
    """Integration tests for CORS headers."""

    def test_cors_headers_present(self, client):
        """Test that CORS headers are present in responses."""
        response = client.options(
            "/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET"
            }
        )
        # CORS preflight should return 200 or headers should be present
        assert response.status_code in [200, 400]


class TestContentNegotiation:
    """Integration tests for content negotiation."""

    def test_json_content_type(self, client):
        """Test that API returns JSON content type."""
        response = client.get("/health")
        assert "application/json" in response.headers.get("content-type", "")

    def test_accepts_json_request(self, client):
        """Test that API accepts JSON requests."""
        response = client.get(
            "/health",
            headers={"Accept": "application/json"}
        )
        assert response.status_code == 200
        assert "application/json" in response.headers.get("content-type", "")
