"""
Tests for app.core.middleware.

The focus is the CORS policy: ``allow_origins=["*"]`` together with
``allow_credentials=True`` is invalid per the CORS specification. Starlette
implements that combination by reflecting the request ``Origin`` back with
``Access-Control-Allow-Credentials: true``, which means any website on the
internet can make credentialed cross-origin calls to the API.
"""
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.middleware import (
    CORSConfigurationError,
    resolve_cors_policy,
    setup_middleware,
)


def make_settings(environment="development", origins=None):
    """Build a settings stub with just the fields setup_middleware reads."""
    settings = MagicMock()
    settings.ENVIRONMENT = environment
    settings.CORS_ORIGINS = ["*"] if origins is None else origins
    settings.CORS_METHODS = ["*"]
    settings.CORS_HEADERS = ["*"]
    return settings


class TestResolveCorsPolicy:
    """Unit tests for the resolved CORS policy."""

    def test_wildcard_never_pairs_with_credentials(self):
        policy = resolve_cors_policy(make_settings("development", ["*"]))

        assert policy["allow_origins"] == ["*"]
        assert policy["allow_credentials"] is False, (
            "wildcard origins must never be combined with credentials"
        )

    def test_wildcard_among_other_origins_still_disables_credentials(self):
        policy = resolve_cors_policy(
            make_settings("development", ["https://app.example.com", "*"])
        )

        assert policy["allow_credentials"] is False

    def test_explicit_origins_allow_credentials(self):
        policy = resolve_cors_policy(
            make_settings("development", ["https://app.example.com"])
        )

        assert policy["allow_origins"] == ["https://app.example.com"]
        assert policy["allow_credentials"] is True

    def test_wildcard_is_refused_in_production(self):
        with pytest.raises(CORSConfigurationError) as excinfo:
            resolve_cors_policy(make_settings("production", ["*"]))

        assert "CORS_ORIGINS" in str(excinfo.value)

    def test_explicit_origins_accepted_in_production(self):
        policy = resolve_cors_policy(
            make_settings("production", ["https://app.headwater.com"])
        )

        assert policy["allow_credentials"] is True

    def test_empty_origin_list_is_not_a_wildcard(self):
        policy = resolve_cors_policy(make_settings("production", []))

        assert policy["allow_origins"] == []
        assert policy["allow_credentials"] is True


class TestCorsResponses:
    """End-to-end assertions against the middleware Starlette actually runs."""

    @staticmethod
    def build_app(settings):
        app = FastAPI()

        @app.get("/probe")
        async def probe():
            return {"ok": True}

        setup_middleware(app, settings)
        return app

    def test_wildcard_response_does_not_allow_credentials(self):
        """
        The regression itself: before the fix this response carried both a
        reflected Origin and Access-Control-Allow-Credentials: true.
        """
        client = TestClient(self.build_app(make_settings("development", ["*"])))

        response = client.get("/probe", headers={"Origin": "https://evil.example"})

        assert response.status_code == 200
        assert "access-control-allow-credentials" not in response.headers
        assert response.headers.get("access-control-allow-origin") != "https://evil.example"

    def test_explicit_allow_list_rejects_unknown_origin(self):
        client = TestClient(
            self.build_app(make_settings("development", ["https://app.example.com"]))
        )

        allowed = client.get("/probe", headers={"Origin": "https://app.example.com"})
        assert allowed.headers.get("access-control-allow-origin") == "https://app.example.com"
        assert allowed.headers.get("access-control-allow-credentials") == "true"

        denied = client.get("/probe", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in denied.headers

    def test_setup_middleware_refuses_wildcard_in_production(self):
        with pytest.raises(CORSConfigurationError):
            self.build_app(make_settings("production", ["*"]))
