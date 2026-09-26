"""Upstream rate limiting is a 429 problem, not a 502 or a 500.

Covers the pieces in ``app.core.exceptions`` that every upstream shares:
``parse_retry_after``, ``UpstreamRateLimitedError`` and the handler that turns
it into an RFC 7807 ``application/problem+json`` response with ``Retry-After``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.exceptions import (
    UpstreamRateLimitedError,
    configure_exception_handlers,
    parse_retry_after,
)


class TestParseRetryAfter:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("120", 120),
            (" 30 ", 30),
            ("0", 1),  # never below 1, the same floor Headwater's own limiter uses
            (None, None),
            ("", None),
            ("soon", None),
            ("-5", None),
            ("1.5", None),
            ("²", None),  # str.isdigit() is True for this, int() is not
        ],
    )
    def test_delay_seconds(self, value, expected):
        assert parse_retry_after(value) == expected

    def test_http_date_is_converted_to_seconds_from_now(self):
        now = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)
        assert parse_retry_after("Fri, 25 Sep 2026 12:01:30 GMT", now=now) == 90

    def test_http_date_in_the_past_is_one_second(self):
        now = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)
        assert parse_retry_after("Fri, 25 Sep 2026 11:00:00 GMT", now=now) == 1


class TestUpstreamRateLimitedError:
    def test_is_an_http_exception_so_router_catch_alls_pass_it_through(self):
        # Every router re-raises HTTPException past its `except Exception`.
        assert issubclass(UpstreamRateLimitedError, HTTPException)

    def test_uses_the_upstream_retry_after(self):
        exc = UpstreamRateLimitedError("Google Trends", retry_after=120)
        assert exc.status_code == 429
        assert exc.headers == {"Retry-After": "120"}
        assert exc.to_dict() == {
            "type": "https://headwater.com/problems/upstream_rate_limited",
            "title": "Too Many Requests",
            "status": 429,
            "detail": "Google Trends is rate limiting requests from this server. Retry after 120 seconds.",
            "upstream": "Google Trends",
            "retry_after": 120,
        }

    def test_falls_back_to_the_configured_default(self, override_settings):
        override_settings(UPSTREAM_RETRY_AFTER_SECONDS=17)
        exc = UpstreamRateLimitedError("Google News")
        assert exc.retry_after == 17
        assert exc.headers == {"Retry-After": "17"}

    def test_default_setting_is_sixty_seconds(self, settings):
        assert settings.UPSTREAM_RETRY_AFTER_SECONDS == 60


class TestUpstreamRateLimitedHandler:
    @pytest.fixture
    def client(self):
        app = FastAPI()
        configure_exception_handlers(app)

        @app.get("/throttled")
        async def throttled():
            raise UpstreamRateLimitedError("Google Trends", retry_after=42)

        @app.get("/swallowed")
        async def swallowed():
            # The shape of every router: HTTPException re-raised past the catch-all.
            try:
                raise UpstreamRateLimitedError("Google News", retry_after=5)
            except HTTPException:
                raise
            except Exception as exc:  # pragma: no cover - must not be reached
                raise HTTPException(status_code=500, detail="Internal Server Error") from exc

        return TestClient(app)

    def test_answers_429_problem_json_with_retry_after(self, client):
        response = client.get("/throttled")

        assert response.status_code == 429
        assert response.headers["Retry-After"] == "42"
        assert response.headers["Content-Type"].startswith("application/problem+json")
        body = response.json()
        assert body["type"] == "https://headwater.com/problems/upstream_rate_limited"
        assert body["title"] == "Too Many Requests"
        assert body["status"] == 429
        assert body["upstream"] == "Google Trends"
        assert body["retry_after"] == 42

    def test_survives_a_router_catch_all(self, client):
        response = client.get("/swallowed")
        assert response.status_code == 429
        assert response.json()["upstream"] == "Google News"

    def test_logged_as_a_warning_not_an_error(self, client, caplog):
        with caplog.at_level(logging.DEBUG, logger="app.core.exceptions"):
            client.get("/throttled")

        records = [r for r in caplog.records if r.name == "app.core.exceptions"]
        assert records, "the handler should log the upstream rate limit"
        assert all(r.levelno == logging.WARNING for r in records)
