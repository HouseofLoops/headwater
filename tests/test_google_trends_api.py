"""
Comprehensive tests for Google Trends API endpoints.

This module provides extensive test coverage for all Google Trends API endpoints,
including success cases, error handling, caching, and edge cases.
"""

import json
import logging
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient
from trendspy.client import BATCH_URL, TrendsQuotaExceededError

# Import the router and utility functions
from app.api.google_trends import google_trends_api as trends_api
from app.api.google_trends.google_trends_api import (
    BATCH_PERIOD_BY_TIMEFRAME,
    NEWS_TOKENS_REJECTED_DETAIL,
    REFERER_LIST,
    UPSTREAM_REJECTED_DETAIL,
    UPSTREAM_UNAVAILABLE_DETAIL,
    UPSTREAM_UNUSABLE_DETAIL,
    USER_AGENT_LIST,
    HumanFriendlyBatchPeriod,
    InvalidNewsTokens,
    UpstreamRateLimited,
    UpstreamRejected,
    classify_trends_failure,
    df_to_json,
    get_random_headers,
    get_trends_instance,
    google_trends_router,
    parse_news_tokens,
    to_jsonable,
)
from app.core import cache_manager as cache_manager_module
from app.core.exceptions import configure_exception_handlers
from app.core.rate_limiter import rate_limit


class Unserializable:
    """An object no JSON encoder can turn into a document.

    ``__slots__`` with no fields means both ``dict(obj)`` and ``vars(obj)``
    raise, which is what makes ``jsonable_encoder`` give up. A plain empty
    class would NOT do: ``vars()`` returns ``{}`` and it encodes happily as an
    empty object, so a test using one proves nothing about error handling.
    """

    __slots__ = ()


# -------------------------------------------------------------------------
# Mocked Google upstream
#
# These build real ``requests.Response`` objects and hand them to a real
# HeadwaterTrends through a stub session, so trendspy's own request and parse
# code runs, and nothing leaves the process.
# -------------------------------------------------------------------------
NEWS_PAYLOAD = json.dumps(
    [[["Title A", "https://example.com/a", "Example News", [1790386237], "https://img.example/a.jpg"]]]
)
NEWS_ARTICLE = {
    "title": "Title A",
    "url": "https://example.com/a",
    "source": "Example News",
    "picture": "https://img.example/a.jpg",
    "time": 1790386237,
    "snippet": None,
}
REASONS = {200: "OK", 400: "Bad Request", 429: "Too Many Requests", 500: "Internal Server Error"}


def http_response(status, body="", headers=None, url="https://trends.google.com/trends/api"):
    """A ``requests.Response`` as trendspy would receive it."""
    response = requests.Response()
    response.status_code = status
    response.reason = REASONS.get(status, "")
    response._content = body.encode()
    response.headers.update({"Content-Type": "application/json; charset=utf-8", **(headers or {})})
    response.url = url
    return response


def batch_response(entries, status=200, headers=None):
    """A batchexecute answer: the XSSI prefix, then the envelope on the last line."""
    return http_response(status, ")]}'\n\n" + json.dumps(entries), headers, url=BATCH_URL)


def rpc_entry(rpc_id, payload, rpc_status=None):
    """One ``wrb.fr`` result; ``payload`` is None when Google refuses the arguments."""
    return ["wrb.fr", rpc_id, payload, None, None, rpc_status, "generic"]


def fake_trends(get=None, post=None):
    """A real HeadwaterTrends whose session answers with canned responses.

    A single response is returned for every call (trendspy retries a 429
    itself); a list is consumed in order.
    """
    trends = trends_api.HeadwaterTrends(request_delay=0)
    trends.session = MagicMock()
    for method, canned in (("get", get), ("post", post)):
        mock = getattr(trends.session, method)
        if isinstance(canned, list):
            mock.side_effect = canned
        elif canned is not None:
            mock.return_value = canned
    return trends


def posted_rpc_args(trends):
    """Decode the arguments HeadwaterTrends posted to batchexecute."""
    post_data = trends.session.post.call_args.args[1]
    envelope = json.loads(post_data.removeprefix("f.req="))
    return json.loads(envelope[0][0][1])


@pytest.fixture
def quiet_trends(monkeypatch):
    """Empty cache, and no real sleeping in trendspy's 429 backoff."""
    monkeypatch.setattr("trendspy.client.sleep", lambda _seconds: None)
    cache_manager_module._cache_store.clear()
    yield
    cache_manager_module._cache_store.clear()


@pytest.fixture
def problem_client():
    """The Trends router behind Headwater's real RFC 7807 exception handlers."""
    app = FastAPI()
    configure_exception_handlers(app)
    app.include_router(google_trends_router, prefix="/api/v1/google-trends")
    app.dependency_overrides[rate_limit] = lambda: None
    return TestClient(app)


class TestGoogleTrendsAPI:
    """Test class for Google Trends API endpoints."""

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """Start every test with an empty process cache.

        These endpoints cache on (endpoint, query parameters), and several
        tests below use the same parameters, so without this a later test
        reads the earlier test's cached answer instead of exercising its own
        mock. That leakage is the same mechanism as the production bug this
        module was fixed for, just inside the test session.
        """
        cache_manager_module._cache_store.clear()
        yield
        cache_manager_module._cache_store.clear()

    @pytest.fixture
    def client(self):
        """Create a test client for the Google Trends router."""
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(google_trends_router, prefix="/api/v1/google-trends")
        return TestClient(app)

    @pytest.fixture
    def mock_trends_instance(self):
        """Mock Trends instance for testing."""
        mock_instance = MagicMock()
        mock_instance.interest_over_time.return_value = pd.DataFrame(
            {"date": pd.date_range("2023-01-01", periods=5), "python": [50, 55, 60, 58, 62]}
        )
        mock_instance.interest_by_region.return_value = pd.DataFrame(
            {"geoName": ["United States", "United Kingdom", "Canada"], "python": [100, 80, 70]}
        )
        mock_instance.related_queries.return_value = {
            "python": {
                "top": [{"query": "python programming", "value": 100}],
                "rising": [{"query": "python tutorial", "value": 150}],
            }
        }
        mock_instance.related_topics.return_value = {
            "python": {
                "top": [{"topic": "Programming Language", "value": 100}],
                "rising": [{"topic": "Data Science", "value": 120}],
            }
        }
        mock_instance.trending_now.return_value = [{"title": "Python", "formattedTraffic": "1M+", "articles": []}]
        mock_instance.trending_now_by_rss.return_value = [{"title": "Python", "newsItems": []}]
        mock_instance.trending_now_showcase_timeline.return_value = {"python": [{"time": "2023-01-01", "value": 50}]}
        mock_instance.categories.return_value = [{"id": "13", "name": "Computers & Electronics"}]
        mock_instance.geo.return_value = [{"id": "US", "name": "United States"}]
        return mock_instance

    @pytest.fixture
    def mock_cache(self):
        """Mock cache functions."""
        with (
            patch("app.api.google_trends.google_trends_api.get_cached_or_fetch") as mock_cache,
            patch("app.api.google_trends.google_trends_api.generate_cache_key") as mock_key,
        ):
            mock_key.return_value = "test_cache_key"
            mock_cache.return_value = {"data": "cached_result"}
            yield mock_cache

    @pytest.fixture
    def mock_get_instance(self):
        """Mock get_trends_instance function."""
        with patch("app.api.google_trends.google_trends_api.get_trends_instance") as mock_instance:
            yield mock_instance

    # Test utility functions first
    def test_get_random_headers(self):
        """Test random header generation."""
        headers = get_random_headers()

        assert isinstance(headers, dict)
        assert "Referer" in headers
        assert "User-Agent" in headers
        assert "Accept-Language" in headers
        assert "Accept-Encoding" in headers
        assert "Connection" in headers

        assert headers["Referer"] in REFERER_LIST
        assert headers["User-Agent"] in USER_AGENT_LIST
        assert headers["Accept-Language"] == "en-US,en;q=0.9"
        assert headers["Accept-Encoding"] == "gzip, deflate, br"
        assert headers["Connection"] == "keep-alive"

    def test_df_to_json_empty_dataframe(self):
        """Test df_to_json with empty DataFrame."""
        df = pd.DataFrame()
        result = df_to_json(df)
        assert result == []

    def test_df_to_json_with_data(self):
        """Test df_to_json with data."""
        df = pd.DataFrame({"name": ["Alice", "Bob"], "age": [25, 30]})
        result = df_to_json(df)
        expected = [{"name": "Alice", "age": 25}, {"name": "Bob", "age": 30}]
        assert result == expected

    def test_to_jsonable_dataframe(self):
        """Test to_jsonable with DataFrame."""
        df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
        result = to_jsonable(df)
        expected = [{"a": 1, "b": 3}, {"a": 2, "b": 4}]
        assert result == expected

    def test_to_jsonable_numpy_int(self):
        """Test to_jsonable with numpy int."""
        result = to_jsonable(np.int64(42))
        assert result == 42
        assert isinstance(result, int)

    def test_to_jsonable_numpy_float(self):
        """Test to_jsonable with numpy float."""
        result = to_jsonable(np.float64(3.14))
        assert abs(result - 3.14) < 1e-10  # Use approximate comparison for floats
        assert isinstance(result, float)

    def test_to_jsonable_numpy_array(self):
        """Test to_jsonable with numpy array."""
        arr = np.array([1, 2, 3])
        result = to_jsonable(arr)
        assert result == [1, 2, 3]

    def test_to_jsonable_dict(self):
        """Test to_jsonable with dict containing numpy values."""
        data = {"a": np.int64(1), "b": np.float64(2.5)}
        result = to_jsonable(data)
        expected = {"a": 1, "b": 2.5}
        assert result == expected

    def test_to_jsonable_list(self):
        """Test to_jsonable with list containing numpy values."""
        data = [np.int64(1), np.float64(2.5)]
        result = to_jsonable(data)
        expected = [1, 2.5]
        assert result == expected

    def test_to_jsonable_string(self):
        """Test to_jsonable with regular string."""
        result = to_jsonable("hello")
        assert result == "hello"

    @pytest.mark.asyncio
    async def test_get_trends_instance_no_proxy(self, mock_trends_instance):
        """Test get_trends_instance without proxy."""
        with (
            patch("app.api.google_trends.google_trends_api.get_proxy", return_value=None),
            patch("app.api.google_trends.google_trends_api.HeadwaterTrends", return_value=mock_trends_instance),
        ):
            result = await get_trends_instance()

            assert result is mock_trends_instance

    @pytest.mark.asyncio
    async def test_get_trends_instance_with_proxy(self, mock_trends_instance):
        """Test get_trends_instance with proxy."""
        with (
            patch("app.api.google_trends.google_trends_api.get_proxy", return_value="http://proxy.example.com:8080"),
            patch("app.api.google_trends.google_trends_api.HeadwaterTrends", return_value=mock_trends_instance),
        ):
            result = await get_trends_instance()

            assert result is mock_trends_instance

    # Test API endpoints
    @patch("app.api.google_trends.google_trends_api.get_trends_instance")
    def test_interest_over_time_success(self, mock_get_instance, client):
        """Test interest over time endpoint success."""
        mock_instance = MagicMock()
        mock_instance.interest_over_time.return_value = pd.DataFrame(
            {"date": pd.date_range("2023-01-01", periods=3), "python": [50, 55, 60]}
        )
        mock_get_instance.return_value = mock_instance

        response = client.get("/api/v1/google-trends/interest-over-time?keywords=python")

        assert response.status_code == 200
        data = response.json()
        assert "data" in data

    def test_interest_over_time_no_keywords(self, client):
        """Test interest over time with no keywords."""
        response = client.get("/api/v1/google-trends/interest-over-time?keywords=")

        assert response.status_code == 400
        data = response.json()
        assert "No valid keywords provided" in data["detail"]

    @patch("app.api.google_trends.google_trends_api.get_trends_instance")
    def test_interest_over_time_empty_dataframe(self, mock_get_instance, client):
        """Test interest over time with empty DataFrame response."""
        mock_instance = MagicMock()
        mock_instance.interest_over_time.return_value = pd.DataFrame()
        mock_get_instance.return_value = mock_instance

        response = client.get("/api/v1/google-trends/interest-over-time?keywords=python")

        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "No data returned from Google Trends."

    @patch("app.api.google_trends.google_trends_api.get_trends_instance")
    def test_interest_by_region_success(self, mock_get_instance, client):
        """Test interest by region endpoint success."""
        mock_instance = MagicMock()
        mock_instance.interest_by_region.return_value = pd.DataFrame({"geoName": ["US", "UK"], "python": [100, 80]})
        mock_get_instance.return_value = mock_instance

        response = client.get("/api/v1/google-trends/interest-by-region?keyword=python")

        assert response.status_code == 200
        data = response.json()
        assert "data" in data

    @patch("app.api.google_trends.google_trends_api.get_trends_instance")
    def test_related_queries_success(self, mock_get_instance, client):
        """Test related queries endpoint success."""
        mock_instance = MagicMock()
        mock_instance.related_queries.return_value = {
            "python": {"top": [{"query": "python programming", "value": 100}]}
        }
        mock_get_instance.return_value = mock_instance

        response = client.get("/api/v1/google-trends/related-queries?keyword=python")

        assert response.status_code == 200
        data = response.json()
        assert "data" in data

    @patch("app.api.google_trends.google_trends_api.get_trends_instance")
    def test_related_topics_success(self, mock_get_instance, client):
        """Test related topics endpoint success."""
        mock_instance = MagicMock()
        mock_instance.related_topics.return_value = {"python": {"top": [{"topic": "Programming", "value": 100}]}}
        mock_get_instance.return_value = mock_instance

        response = client.get("/api/v1/google-trends/related-topics?keyword=python")

        assert response.status_code == 200
        data = response.json()
        assert "data" in data

    @patch("app.api.google_trends.google_trends_api.get_trends_instance")
    def test_trending_now_success(self, mock_get_instance, client):
        """Test trending now endpoint success."""
        mock_instance = MagicMock()
        mock_instance.trending_now.return_value = [{"title": "Python", "formattedTraffic": "1M+"}]
        mock_get_instance.return_value = mock_instance

        response = client.get("/api/v1/google-trends/trending-now")

        assert response.status_code == 200
        data = response.json()
        assert "data" in data

    @patch("app.api.google_trends.google_trends_api.get_trends_instance")
    def test_trending_now_by_rss_success(self, mock_get_instance, client):
        """Test trending now by RSS endpoint success."""
        mock_instance = MagicMock()
        mock_instance.trending_now_by_rss.return_value = [{"title": "Python", "newsItems": []}]
        mock_get_instance.return_value = mock_instance

        response = client.get("/api/v1/google-trends/trending-now-by-rss")

        assert response.status_code == 200
        data = response.json()
        assert "data" in data

    @patch("app.api.google_trends.google_trends_api.get_trends_instance")
    def test_trending_now_news_by_ids_success(self, mock_get_instance, client):
        """Articles come back as dicts; the old normaliser rejected every success as a bad shape."""
        trends = fake_trends(post=[batch_response([rpc_entry("w4opAf", NEWS_PAYLOAD)])])
        mock_get_instance.return_value = trends

        response = client.get("/api/v1/google-trends/trending-now-news-by-ids?news_tokens=4830466997")

        assert response.status_code == 200, response.text
        assert response.json()["data"] == [NEWS_ARTICLE]

    def test_trending_now_news_by_ids_no_tokens(self, client):
        """Test trending now news by IDs with no tokens."""
        response = client.get("/api/v1/google-trends/trending-now-news-by-ids?news_tokens=")

        assert response.status_code == 400  # Bad request for no valid tokens
        data = response.json()
        assert "detail" in data

    @patch("app.api.google_trends.google_trends_api.get_trends_instance")
    def test_trending_now_showcase_timeline_success(self, mock_get_instance, client):
        """Test trending now showcase timeline endpoint success."""
        mock_instance = MagicMock()
        mock_instance.trending_now_showcase_timeline.return_value = {"python": [{"time": "2023-01-01", "value": 50}]}
        mock_get_instance.return_value = mock_instance

        response = client.get("/api/v1/google-trends/trending-now-showcase-timeline?keywords=python&timeframe=past_24h")

        assert response.status_code == 200
        data = response.json()
        assert "data" in data

    def test_trending_now_showcase_timeline_no_keywords(self, client):
        """Test trending now showcase timeline with no keywords."""
        response = client.get("/api/v1/google-trends/trending-now-showcase-timeline?keywords=&timeframe=past_24h")

        assert response.status_code == 400  # Bad request for no valid keywords
        data = response.json()
        assert "detail" in data

    def test_trending_now_showcase_timeline_invalid_timeframe(self, client):
        """Test trending now showcase timeline with invalid timeframe."""
        response = client.get("/api/v1/google-trends/trending-now-showcase-timeline?keywords=python&timeframe=invalid")

        assert response.status_code == 422  # Validation error
        data = response.json()
        assert "detail" in data

    @patch("app.api.google_trends.google_trends_api.get_trends_instance")
    def test_categories_success(self, mock_get_instance, client):
        """Test categories endpoint success."""
        mock_instance = MagicMock()
        mock_instance.categories.return_value = [{"id": "13", "name": "Computers & Electronics"}]
        mock_get_instance.return_value = mock_instance

        response = client.get("/api/v1/google-trends/categories")

        assert response.status_code == 200
        data = response.json()
        assert "data" in data

    @patch("app.api.google_trends.google_trends_api.get_trends_instance")
    def test_geo_success(self, mock_get_instance, client):
        """Test geo endpoint success."""
        mock_instance = MagicMock()
        mock_instance.geo.return_value = [{"id": "US", "name": "United States"}]
        mock_get_instance.return_value = mock_instance

        response = client.get("/api/v1/google-trends/geo")

        assert response.status_code == 200
        data = response.json()
        assert "data" in data

    @patch("app.api.google_trends.google_trends_api.get_trends_instance")
    def test_upstream_failure_returns_502(self, mock_get_instance, client):
        """An upstream failure must be reported, not disguised as empty data.

        This test previously asserted 200 with a message, i.e. it encoded the
        bug: a failed Google Trends call was presented to the caller as a
        successful empty result. An empty 200 that should have been a 502 is
        indistinguishable from "there is genuinely no data", so clients cannot
        retry and monitoring sees a healthy endpoint.
        """
        mock_instance = MagicMock()
        mock_instance.interest_over_time.side_effect = Exception("API Error")
        mock_get_instance.return_value = mock_instance

        response = client.get("/api/v1/google-trends/interest-over-time?keywords=python")

        assert response.status_code == 502
        assert response.json()["detail"] == ("Upstream Google Trends request failed. Please retry.")
        # The upstream error text must not reach the caller.
        assert "API Error" not in response.text

    @patch("app.api.google_trends.google_trends_api.get_trends_instance")
    def test_upstream_failure_is_not_cached(self, mock_get_instance, client):
        """A failed call must leave the cache untouched.

        The original code answered ``{"data": []}`` with HTTP 200, which
        ``get_cached_or_fetch`` then stored for the full hour-long TTL: one
        upstream blip served empty results to every caller until it expired.
        The recovery here is that the very next request calls upstream again
        and sees the recovered data.
        """
        mock_instance = MagicMock()
        mock_instance.interest_over_time.side_effect = Exception("API Error")
        mock_get_instance.return_value = mock_instance

        first = client.get("/api/v1/google-trends/interest-over-time?keywords=python")
        assert first.status_code == 502
        assert cache_manager_module._cache_store == {}

        # Upstream recovers; the next request must reflect that immediately.
        mock_instance.interest_over_time.side_effect = None
        mock_instance.interest_over_time.return_value = pd.DataFrame(
            {"date": pd.date_range("2023-01-01", periods=2), "python": [50, 55]}
        )

        second = client.get("/api/v1/google-trends/interest-over-time?keywords=python")
        assert second.status_code == 200
        assert len(second.json()["data"]) == 2

    @patch("app.api.google_trends.google_trends_api.get_trends_instance")
    def test_empty_upstream_result_is_cached_as_success(self, mock_get_instance, client):
        """An empty answer is a real answer, so it may be cached.

        The counterpart to the test above: "Google Trends has no data for this
        keyword" is a successful 200 and caching it is correct. Only failures
        must bypass the cache.
        """
        mock_instance = MagicMock()
        mock_instance.interest_over_time.return_value = pd.DataFrame()
        mock_get_instance.return_value = mock_instance

        response = client.get("/api/v1/google-trends/interest-over-time?keywords=python")

        assert response.status_code == 200
        assert response.json() == {
            "data": [],
            "message": "No data returned from Google Trends.",
        }
        assert cache_manager_module._cache_store != {}

    @patch("app.api.google_trends.google_trends_api.get_trends_instance")
    def test_caching_behavior(self, mock_get_instance, client):
        """Test that caching is properly implemented."""
        mock_instance = MagicMock()
        mock_instance.interest_over_time.return_value = pd.DataFrame(
            {"date": pd.date_range("2023-01-01", periods=3), "python": [50, 55, 60]}
        )
        mock_get_instance.return_value = mock_instance

        with (
            patch("app.api.google_trends.google_trends_api.generate_cache_key") as mock_key,
            patch("app.api.google_trends.google_trends_api.get_cached_or_fetch") as mock_cache,
        ):
            mock_key.return_value = "test_key"
            mock_cache.return_value = {"data": "cached_data"}

            response = client.get("/api/v1/google-trends/interest-over-time?keywords=python")

            assert response.status_code == 200
            # Note: The actual caching behavior depends on the implementation

    # Test parameter validation
    def test_parameter_validation(self, client):
        """Test parameter validation for various endpoints."""
        # Test interest over time with missing required parameter
        response = client.get("/api/v1/google-trends/interest-over-time")
        assert response.status_code == 422  # Validation error

        # Test interest by region with missing required parameter
        response = client.get("/api/v1/google-trends/interest-by-region")
        assert response.status_code == 422  # Validation error

        # Test related queries with missing required parameter
        response = client.get("/api/v1/google-trends/related-queries")
        assert response.status_code == 422  # Validation error

    # Test enum values
    def test_human_friendly_batch_period_enum(self):
        """Every member the router references must exist under that name.

        The timeline endpoint returned 500 on every request for as long as it
        existed because the router said ``HumanFriendlyBatchPeriod.past_4h``
        while the enum only defined ``PAST_4H``. A plain attribute-access test
        like this one catches that class of typo the moment it is written.
        """
        assert HumanFriendlyBatchPeriod.past_4h.value == "past_4h"
        assert HumanFriendlyBatchPeriod.past_24h.value == "past_24h"
        assert HumanFriendlyBatchPeriod.past_48h.value == "past_48h"
        assert HumanFriendlyBatchPeriod.past_7d.value == "past_7d"

    def test_human_friendly_batch_period_uppercase_aliases(self):
        """The SCREAMING_CASE spellings resolve to the same members."""
        assert HumanFriendlyBatchPeriod.PAST_4H is HumanFriendlyBatchPeriod.past_4h
        assert HumanFriendlyBatchPeriod.PAST_24H is HumanFriendlyBatchPeriod.past_24h
        assert HumanFriendlyBatchPeriod.PAST_48H is HumanFriendlyBatchPeriod.past_48h
        assert HumanFriendlyBatchPeriod.PAST_7D is HumanFriendlyBatchPeriod.past_7d

    def test_every_batch_period_is_mapped(self):
        """No enum member may be missing from the trendspy mapping.

        A member the mapping does not cover means a query string FastAPI
        accepts but the handler cannot serve. The module raises at import if
        this ever drifts; the assertion states the invariant here too.
        """
        assert set(BATCH_PERIOD_BY_TIMEFRAME) == set(HumanFriendlyBatchPeriod)

    @pytest.mark.parametrize("timeframe", [member.value for member in HumanFriendlyBatchPeriod])
    @patch("app.api.google_trends.google_trends_api.get_trends_instance")
    def test_timeline_accepts_every_timeframe(self, mock_get_instance, client, timeframe):
        """Each advertised timeframe must actually reach trendspy."""
        mock_instance = MagicMock()
        mock_instance.trending_now_showcase_timeline.return_value = {"python": [{"time": "2023-01-01", "value": 50}]}
        mock_get_instance.return_value = mock_instance

        response = client.get(
            f"/api/v1/google-trends/trending-now-showcase-timeline?keywords=python&timeframe={timeframe}"
        )

        assert response.status_code == 200, response.text
        assert "data" in response.json()

    # Test edge cases
    @patch("app.api.google_trends.google_trends_api.get_trends_instance")
    def test_empty_api_response_handling(self, mock_get_instance, client):
        """Test handling of empty API responses."""
        mock_instance = MagicMock()
        mock_instance.interest_over_time.return_value = None
        mock_get_instance.return_value = mock_instance

        response = client.get("/api/v1/google-trends/interest-over-time?keywords=python")

        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "No data returned from Google Trends."

    @patch("app.api.google_trends.google_trends_api.get_trends_instance")
    def test_unserializable_upstream_response_returns_502(self, mock_get_instance, client):
        """A response we cannot encode is an upstream problem, not empty data.

        Two things changed from the original version of this test. The fixture
        is now genuinely unencodable (see :class:`Unserializable`); the old one
        had an empty ``__dict__`` and encoded fine as ``{}``, so the test never
        reached the error path it claimed to cover. And the expectation is now
        502 rather than 200-with-a-message: we did not get a usable answer, so
        saying so beats caching an empty success for an hour.
        """
        mock_instance = MagicMock()
        mock_instance.interest_over_time.return_value = Unserializable()
        mock_get_instance.return_value = mock_instance

        response = client.get("/api/v1/google-trends/interest-over-time?keywords=python")

        assert response.status_code == 502
        assert response.json()["detail"] == ("Upstream Google Trends returned an unusable response.")
        assert cache_manager_module._cache_store == {}


# -------------------------------------------------------------------------
# Google rate limiting -> 429, genuine failures -> 502
# -------------------------------------------------------------------------
@pytest.mark.usefixtures("quiet_trends")
class TestUpstreamRateLimiting:
    """Google throttling Headwater is a 429 with Retry-After, not a 502.

    Before this, related-queries and related-topics answered 502 "Upstream
    Google Trends request failed" to every quota error, and each one was logged
    with a traceback as if Headwater were broken.
    """

    def test_http_429_with_retry_after_is_passed_on(self, problem_client):
        trends = fake_trends(get=http_response(429, "<html>sorry</html>", {"Retry-After": "120"}))

        with patch.object(trends_api, "get_trends_instance", return_value=trends):
            response = problem_client.get("/api/v1/google-trends/related-queries?keyword=python")

        assert response.status_code == 429, response.text
        assert response.headers["Retry-After"] == "120"
        assert response.headers["Content-Type"].startswith("application/problem+json")
        body = response.json()
        assert body["type"] == "https://headwater.com/problems/upstream_rate_limited"
        assert body["title"] == "Too Many Requests"
        assert body["status"] == 429
        assert body["upstream"] == "Google Trends"
        assert body["retry_after"] == 120
        # A rate limit is a failure: nothing may be cached.
        assert cache_manager_module._cache_store == {}

    def test_quota_error_without_retry_after_uses_the_configured_default(self, problem_client, override_settings):
        override_settings(UPSTREAM_RETRY_AFTER_SECONDS=45)
        mock_instance = MagicMock()
        mock_instance.related_topics.side_effect = TrendsQuotaExceededError()

        with patch.object(trends_api, "get_trends_instance", return_value=mock_instance):
            response = problem_client.get("/api/v1/google-trends/related-topics?keyword=python")

        assert response.status_code == 429, response.text
        assert response.headers["Retry-After"] == "45"
        assert response.json()["retry_after"] == 45

    def test_batch_endpoint_429_without_retry_after_uses_the_default(self, problem_client):
        """trendspy never checks the batchexecute status; HeadwaterTrends does."""
        trends = fake_trends(post=http_response(429, "<html>sorry</html>", url=BATCH_URL))

        with patch.object(trends_api, "get_trends_instance", return_value=trends):
            response = problem_client.get("/api/v1/google-trends/trending-now?geo=US")

        assert response.status_code == 429, response.text
        assert response.headers["Retry-After"] == "60"

    def test_rate_limit_is_not_logged_as_an_error(self, problem_client, caplog):
        trends = fake_trends(get=http_response(429, "", {"Retry-After": "5"}))

        with (
            caplog.at_level(logging.DEBUG),
            patch.object(trends_api, "get_trends_instance", return_value=trends),
        ):
            response = problem_client.get("/api/v1/google-trends/related-queries?keyword=python")

        assert response.status_code == 429
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert errors == [], [r.getMessage() for r in errors]
        assert any("rate-limited" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)

    def test_next_request_retries_after_a_rate_limit(self, problem_client):
        trends = fake_trends(get=http_response(429, ""))
        with patch.object(trends_api, "get_trends_instance", return_value=trends):
            first = problem_client.get("/api/v1/google-trends/interest-over-time?keywords=python")
        assert first.status_code == 429

        recovered = MagicMock()
        recovered.interest_over_time.return_value = pd.DataFrame({"python": [1, 2]})
        with patch.object(trends_api, "get_trends_instance", return_value=recovered):
            second = problem_client.get("/api/v1/google-trends/interest-over-time?keywords=python")
        assert second.status_code == 200
        assert len(second.json()["data"]) == 2

    def test_genuine_upstream_failure_is_still_502(self, problem_client):
        trends = fake_trends(get=http_response(500, "boom"))

        with patch.object(trends_api, "get_trends_instance", return_value=trends):
            response = problem_client.get("/api/v1/google-trends/related-queries?keyword=python")

        assert response.status_code == 502, response.text
        assert response.json()["detail"] == UPSTREAM_UNAVAILABLE_DETAIL
        assert "Retry-After" not in response.headers

    def test_http_400_is_502_that_does_not_invite_a_retry(self, problem_client):
        """/trending-now-showcase-timeline: Google rejects every request trendspy builds."""
        rejection = [["er", None, None, None, None, 400, None, None, None, 3], ["di", 11]]
        trends = fake_trends(post=batch_response(rejection, status=400))

        with patch.object(trends_api, "get_trends_instance", return_value=trends):
            response = problem_client.get(
                "/api/v1/google-trends/trending-now-showcase-timeline?keywords=python&timeframe=past_24h"
            )

        assert response.status_code == 502, response.text
        assert response.json()["detail"] == UPSTREAM_REJECTED_DETAIL
        assert "Please retry" not in response.text
        assert cache_manager_module._cache_store == {}


class TestClassifyTrendsFailure:
    def test_http_error_429_carries_retry_after(self):
        error = requests.HTTPError(response=http_response(429, "", {"Retry-After": "90"}))
        failure = classify_trends_failure("op", error)
        assert isinstance(failure, UpstreamRateLimited)
        assert failure.retry_after == 90

    def test_follows_the_exception_chain(self):
        try:
            try:
                raise requests.HTTPError(response=http_response(429, ""))
            except requests.HTTPError as inner:
                raise RuntimeError("wrapped") from inner
        except RuntimeError as outer:
            failure = classify_trends_failure("op", outer)
        assert isinstance(failure, UpstreamRateLimited)
        assert failure.retry_after is None

    def test_http_400_is_rejected(self):
        error = requests.HTTPError(response=http_response(400, ""))
        assert isinstance(classify_trends_failure("op", error), UpstreamRejected)

    @pytest.mark.parametrize("exc", [ValueError("Failed to parse JSON data"), requests.ConnectionError("down")])
    def test_everything_else_is_plain_upstream_failure(self, exc):
        failure = classify_trends_failure("op", exc)
        assert type(failure) is trends_api.UpstreamUnavailable
        assert failure.detail == UPSTREAM_UNAVAILABLE_DETAIL

    def test_headwater_trends_keeps_the_batch_response_on_failure(self):
        trends = fake_trends(post=http_response(429, "", {"Retry-After": "7"}, url=BATCH_URL))
        with pytest.raises(requests.HTTPError) as excinfo:
            trends._get_batch("i0OFE", [None])
        assert excinfo.value.response.status_code == 429
        assert excinfo.value.response.headers["Retry-After"] == "7"


# -------------------------------------------------------------------------
# /trending-now-news-by-ids
# -------------------------------------------------------------------------
@pytest.mark.usefixtures("quiet_trends")
class TestTrendingNewsByIds:
    """Google's news RPC takes [id, language, geo] tokens, not bare strings.

    Forwarding the comma-split input as strings got HTTP 200 with a null
    payload and RPC status [3], and trendspy crashed on json.loads(None):
    "the JSON object must be str, bytes or bytearray, not NoneType".
    """

    URL = "/api/v1/google-trends/trending-now-news-by-ids"

    def _get(self, client, trends, **params):
        with patch.object(trends_api, "get_trends_instance", return_value=trends):
            return client.get(self.URL, params=params)

    def test_bare_ids_are_sent_as_id_language_geo_tokens(self, problem_client):
        trends = fake_trends(post=batch_response([rpc_entry("w4opAf", NEWS_PAYLOAD)]))

        response = self._get(problem_client, trends, news_tokens="4830466997, 4830466998", max_news=2)

        assert response.status_code == 200, response.text
        assert response.json() == {"data": [NEWS_ARTICLE]}
        assert posted_rpc_args(trends) == [[[4830466997, "en", "US"], [4830466998, "en", "US"]], 2]

    def test_geo_completes_bare_ids(self, problem_client):
        trends = fake_trends(post=batch_response([rpc_entry("w4opAf", NEWS_PAYLOAD)]))

        self._get(problem_client, trends, news_tokens="4830466997", geo="gb")

        assert posted_rpc_args(trends)[0] == [[4830466997, "en", "GB"]]

    def test_tokens_as_trending_now_returns_them_are_accepted(self, problem_client):
        trends = fake_trends(post=batch_response([rpc_entry("w4opAf", NEWS_PAYLOAD)]))

        response = self._get(
            problem_client,
            trends,
            news_tokens='[[4830466997, "en", "US"], ["4830466998", "EN", "gb"], [4830466997, "en", "US"]]',
        )

        assert response.status_code == 200, response.text
        # Normalised and de-duplicated.
        assert posted_rpc_args(trends)[0] == [[4830466997, "en", "US"], [4830466998, "en", "GB"]]

    def test_null_payload_is_a_clean_502_not_a_crash(self, problem_client):
        """The exact answer that crashed trendspy on 2026-09-25."""
        trends = fake_trends(post=batch_response([rpc_entry("w4opAf", None, [3]), ["di", 12]]))

        response = self._get(problem_client, trends, news_tokens="4830466997")

        assert response.status_code == 502, response.text
        assert response.headers["Content-Type"].startswith("application/problem+json")
        assert response.json()["detail"] == NEWS_TOKENS_REJECTED_DETAIL
        assert "NoneType" not in response.text
        assert cache_manager_module._cache_store == {}

    @pytest.mark.parametrize("payload", ["[]", "[[]]"])
    def test_unknown_tokens_are_an_empty_result(self, problem_client, payload):
        """trendspy raised IndexError on "[]" (tokens Google does not know)."""
        trends = fake_trends(post=batch_response([rpc_entry("w4opAf", payload)]))

        response = self._get(problem_client, trends, news_tokens="1")

        assert response.status_code == 200, response.text
        assert response.json() == {"data": [], "message": "No news data was returned."}

    @pytest.mark.parametrize(
        "entries",
        [
            [rpc_entry("w4opAf", '{"not": "a list"}')],
            [rpc_entry("w4opAf", "not json")],
            [rpc_entry("w4opAf", json.dumps([[42]]))],
            [rpc_entry("other", NEWS_PAYLOAD)],
            [["di", 12]],
        ],
    )
    def test_unrecognised_answers_are_502_unusable(self, problem_client, entries):
        trends = fake_trends(post=batch_response(entries))

        response = self._get(problem_client, trends, news_tokens="4830466997")

        assert response.status_code == 502, response.text
        assert response.json()["detail"] == UPSTREAM_UNUSABLE_DETAIL

    def test_rate_limit_is_429(self, problem_client):
        trends = fake_trends(post=http_response(429, "", {"Retry-After": "30"}, url=BATCH_URL))

        response = self._get(problem_client, trends, news_tokens="4830466997")

        assert response.status_code == 429, response.text
        assert response.headers["Retry-After"] == "30"

    @pytest.mark.parametrize(
        "news_tokens",
        [
            "zzzz",
            "123,zzzz",
            "12.5",
            ",,,",
            "[not json",
            '{"a": 1}',
            '[["zzzz", "en", "US"]]',
            '[[1, "en"]]',
            '[[true, "en", "US"]]',
            '[[-1, "en", "US"]]',
            '[[1, "english", "US"]]',
            '[[1, "en", "USA1"]]',
            ",".join(str(i) for i in range(51)),
        ],
    )
    def test_malformed_tokens_are_400_and_never_reach_google(self, problem_client, news_tokens):
        trends = fake_trends()

        response = self._get(problem_client, trends, news_tokens=news_tokens)

        assert response.status_code == 400, response.text
        assert response.headers["Content-Type"].startswith("application/problem+json")
        assert "zzzz" not in response.json()["detail"]  # the input is never echoed
        trends.session.post.assert_not_called()

    @pytest.mark.parametrize("max_news", [0, 51])
    def test_max_news_is_bounded(self, problem_client, max_news):
        response = self._get(problem_client, fake_trends(), news_tokens="1", max_news=max_news)
        assert response.status_code == 422

    def test_invalid_geo_is_rejected(self, problem_client):
        response = self._get(problem_client, fake_trends(), news_tokens="1", geo="U$")
        assert response.status_code == 422

    def test_parse_news_tokens_rejects_a_bad_geo_on_its_own(self):
        with pytest.raises(InvalidNewsTokens):
            parse_news_tokens("1", geo="not a geo")
