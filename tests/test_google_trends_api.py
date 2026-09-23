"""
Comprehensive tests for Google Trends API endpoints.

This module provides extensive test coverage for all Google Trends API endpoints,
including success cases, error handling, caching, and edge cases.
"""

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

# Import the router and utility functions
from app.api.google_trends.google_trends_api import (
    BATCH_PERIOD_BY_TIMEFRAME,
    REFERER_LIST,
    USER_AGENT_LIST,
    HumanFriendlyBatchPeriod,
    df_to_json,
    get_random_headers,
    get_trends_instance,
    google_trends_router,
    to_jsonable,
)
from app.core import cache_manager as cache_manager_module


class Unserializable:
    """An object no JSON encoder can turn into a document.

    ``__slots__`` with no fields means both ``dict(obj)`` and ``vars(obj)``
    raise, which is what makes ``jsonable_encoder`` give up. A plain empty
    class would NOT do: ``vars()`` returns ``{}`` and it encodes happily as an
    empty object, so a test using one proves nothing about error handling.
    """

    __slots__ = ()


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
        mock_instance.interest_over_time.return_value = pd.DataFrame({
            'date': pd.date_range('2023-01-01', periods=5),
            'python': [50, 55, 60, 58, 62]
        })
        mock_instance.interest_by_region.return_value = pd.DataFrame({
            'geoName': ['United States', 'United Kingdom', 'Canada'],
            'python': [100, 80, 70]
        })
        mock_instance.related_queries.return_value = {
            'python': {
                'top': [{'query': 'python programming', 'value': 100}],
                'rising': [{'query': 'python tutorial', 'value': 150}]
            }
        }
        mock_instance.related_topics.return_value = {
            'python': {
                'top': [{'topic': 'Programming Language', 'value': 100}],
                'rising': [{'topic': 'Data Science', 'value': 120}]
            }
        }
        mock_instance.trending_now.return_value = [
            {'title': 'Python', 'formattedTraffic': '1M+', 'articles': []}
        ]
        mock_instance.trending_now_by_rss.return_value = [
            {'title': 'Python', 'newsItems': []}
        ]
        mock_instance.trending_now_news_by_ids.return_value = [
            ['token1', 'title1', '{"articles": []}']
        ]
        mock_instance.trending_now_showcase_timeline.return_value = {
            'python': [{'time': '2023-01-01', 'value': 50}]
        }
        mock_instance.categories.return_value = [
            {'id': '13', 'name': 'Computers & Electronics'}
        ]
        mock_instance.geo.return_value = [
            {'id': 'US', 'name': 'United States'}
        ]
        return mock_instance

    @pytest.fixture
    def mock_cache(self):
        """Mock cache functions."""
        with patch('app.api.google_trends.google_trends_api.get_cached_or_fetch') as mock_cache, \
             patch('app.api.google_trends.google_trends_api.generate_cache_key') as mock_key:
            mock_key.return_value = "test_cache_key"
            mock_cache.return_value = {"data": "cached_result"}
            yield mock_cache

    @pytest.fixture
    def mock_get_instance(self):
        """Mock get_trends_instance function."""
        with patch('app.api.google_trends.google_trends_api.get_trends_instance') as mock_instance:
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
        df = pd.DataFrame({
            'name': ['Alice', 'Bob'],
            'age': [25, 30]
        })
        result = df_to_json(df)
        expected = [
            {'name': 'Alice', 'age': 25},
            {'name': 'Bob', 'age': 30}
        ]
        assert result == expected

    def test_to_jsonable_dataframe(self):
        """Test to_jsonable with DataFrame."""
        df = pd.DataFrame({'a': [1, 2], 'b': [3, 4]})
        result = to_jsonable(df)
        expected = [{'a': 1, 'b': 3}, {'a': 2, 'b': 4}]
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
        data = {'a': np.int64(1), 'b': np.float64(2.5)}
        result = to_jsonable(data)
        expected = {'a': 1, 'b': 2.5}
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
        with patch('app.api.google_trends.google_trends_api.get_proxy', return_value=None), \
             patch('app.api.google_trends.google_trends_api.Trends', return_value=mock_trends_instance):

            result = await get_trends_instance()

            assert result is not None

    @pytest.mark.asyncio
    async def test_get_trends_instance_with_proxy(self, mock_trends_instance):
        """Test get_trends_instance with proxy."""
        with patch('app.api.google_trends.google_trends_api.get_proxy', return_value="http://proxy.example.com:8080"), \
             patch('app.api.google_trends.google_trends_api.Trends', return_value=mock_trends_instance):

            result = await get_trends_instance()

            assert result is not None

    # Test API endpoints
    @patch('app.api.google_trends.google_trends_api.get_trends_instance')
    def test_interest_over_time_success(self, mock_get_instance, client):
        """Test interest over time endpoint success."""
        mock_instance = MagicMock()
        mock_instance.interest_over_time.return_value = pd.DataFrame({
            'date': pd.date_range('2023-01-01', periods=3),
            'python': [50, 55, 60]
        })
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

    @patch('app.api.google_trends.google_trends_api.get_trends_instance')
    def test_interest_over_time_empty_dataframe(self, mock_get_instance, client):
        """Test interest over time with empty DataFrame response."""
        mock_instance = MagicMock()
        mock_instance.interest_over_time.return_value = pd.DataFrame()
        mock_get_instance.return_value = mock_instance

        response = client.get("/api/v1/google-trends/interest-over-time?keywords=python")

        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "No data returned from Google Trends."

    @patch('app.api.google_trends.google_trends_api.get_trends_instance')
    def test_interest_by_region_success(self, mock_get_instance, client):
        """Test interest by region endpoint success."""
        mock_instance = MagicMock()
        mock_instance.interest_by_region.return_value = pd.DataFrame({
            'geoName': ['US', 'UK'],
            'python': [100, 80]
        })
        mock_get_instance.return_value = mock_instance

        response = client.get("/api/v1/google-trends/interest-by-region?keyword=python")

        assert response.status_code == 200
        data = response.json()
        assert "data" in data

    @patch('app.api.google_trends.google_trends_api.get_trends_instance')
    def test_related_queries_success(self, mock_get_instance, client):
        """Test related queries endpoint success."""
        mock_instance = MagicMock()
        mock_instance.related_queries.return_value = {
            'python': {
                'top': [{'query': 'python programming', 'value': 100}]
            }
        }
        mock_get_instance.return_value = mock_instance

        response = client.get("/api/v1/google-trends/related-queries?keyword=python")

        assert response.status_code == 200
        data = response.json()
        assert "data" in data

    @patch('app.api.google_trends.google_trends_api.get_trends_instance')
    def test_related_topics_success(self, mock_get_instance, client):
        """Test related topics endpoint success."""
        mock_instance = MagicMock()
        mock_instance.related_topics.return_value = {
            'python': {
                'top': [{'topic': 'Programming', 'value': 100}]
            }
        }
        mock_get_instance.return_value = mock_instance

        response = client.get("/api/v1/google-trends/related-topics?keyword=python")

        assert response.status_code == 200
        data = response.json()
        assert "data" in data

    @patch('app.api.google_trends.google_trends_api.get_trends_instance')
    def test_trending_now_success(self, mock_get_instance, client):
        """Test trending now endpoint success."""
        mock_instance = MagicMock()
        mock_instance.trending_now.return_value = [
            {'title': 'Python', 'formattedTraffic': '1M+'}
        ]
        mock_get_instance.return_value = mock_instance

        response = client.get("/api/v1/google-trends/trending-now")

        assert response.status_code == 200
        data = response.json()
        assert "data" in data

    @patch('app.api.google_trends.google_trends_api.get_trends_instance')
    def test_trending_now_by_rss_success(self, mock_get_instance, client):
        """Test trending now by RSS endpoint success."""
        mock_instance = MagicMock()
        mock_instance.trending_now_by_rss.return_value = [
            {'title': 'Python', 'newsItems': []}
        ]
        mock_get_instance.return_value = mock_instance

        response = client.get("/api/v1/google-trends/trending-now-by-rss")

        assert response.status_code == 200
        data = response.json()
        assert "data" in data

    @patch('app.api.google_trends.google_trends_api.get_trends_instance')
    def test_trending_now_news_by_ids_success(self, mock_get_instance, client):
        """Test trending now news by IDs endpoint success."""
        mock_instance = MagicMock()
        mock_instance.trending_now_news_by_ids.return_value = [
            ['token1', 'title1', '{"articles": [{"title": "Test Article"}]}']
        ]
        mock_get_instance.return_value = mock_instance

        response = client.get("/api/v1/google-trends/trending-now-news-by-ids?news_tokens=token1")

        assert response.status_code == 200
        data = response.json()
        assert "data" in data

    def test_trending_now_news_by_ids_no_tokens(self, client):
        """Test trending now news by IDs with no tokens."""
        response = client.get("/api/v1/google-trends/trending-now-news-by-ids?news_tokens=")

        assert response.status_code == 400  # Bad request for no valid tokens
        data = response.json()
        assert "detail" in data

    @patch('app.api.google_trends.google_trends_api.get_trends_instance')
    def test_trending_now_showcase_timeline_success(self, mock_get_instance, client):
        """Test trending now showcase timeline endpoint success."""
        mock_instance = MagicMock()
        mock_instance.trending_now_showcase_timeline.return_value = {
            'python': [{'time': '2023-01-01', 'value': 50}]
        }
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

    @patch('app.api.google_trends.google_trends_api.get_trends_instance')
    def test_categories_success(self, mock_get_instance, client):
        """Test categories endpoint success."""
        mock_instance = MagicMock()
        mock_instance.categories.return_value = [
            {'id': '13', 'name': 'Computers & Electronics'}
        ]
        mock_get_instance.return_value = mock_instance

        response = client.get("/api/v1/google-trends/categories")

        assert response.status_code == 200
        data = response.json()
        assert "data" in data

    @patch('app.api.google_trends.google_trends_api.get_trends_instance')
    def test_geo_success(self, mock_get_instance, client):
        """Test geo endpoint success."""
        mock_instance = MagicMock()
        mock_instance.geo.return_value = [
            {'id': 'US', 'name': 'United States'}
        ]
        mock_get_instance.return_value = mock_instance

        response = client.get("/api/v1/google-trends/geo")

        assert response.status_code == 200
        data = response.json()
        assert "data" in data

    @patch('app.api.google_trends.google_trends_api.get_trends_instance')
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
        assert response.json()["detail"] == (
            "Upstream Google Trends request failed. Please retry."
        )
        # The upstream error text must not reach the caller.
        assert "API Error" not in response.text

    @patch('app.api.google_trends.google_trends_api.get_trends_instance')
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
        mock_instance.interest_over_time.return_value = pd.DataFrame({
            'date': pd.date_range('2023-01-01', periods=2),
            'python': [50, 55]
        })

        second = client.get("/api/v1/google-trends/interest-over-time?keywords=python")
        assert second.status_code == 200
        assert len(second.json()["data"]) == 2

    @patch('app.api.google_trends.google_trends_api.get_trends_instance')
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

    @patch('app.api.google_trends.google_trends_api.get_trends_instance')
    def test_caching_behavior(self, mock_get_instance, client):
        """Test that caching is properly implemented."""
        mock_instance = MagicMock()
        mock_instance.interest_over_time.return_value = pd.DataFrame({
            'date': pd.date_range('2023-01-01', periods=3),
            'python': [50, 55, 60]
        })
        mock_get_instance.return_value = mock_instance

        with patch('app.api.google_trends.google_trends_api.generate_cache_key') as mock_key, \
             patch('app.api.google_trends.google_trends_api.get_cached_or_fetch') as mock_cache:

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

    @pytest.mark.parametrize(
        "timeframe", [member.value for member in HumanFriendlyBatchPeriod]
    )
    @patch('app.api.google_trends.google_trends_api.get_trends_instance')
    def test_timeline_accepts_every_timeframe(self, mock_get_instance, client, timeframe):
        """Each advertised timeframe must actually reach trendspy."""
        mock_instance = MagicMock()
        mock_instance.trending_now_showcase_timeline.return_value = {
            'python': [{'time': '2023-01-01', 'value': 50}]
        }
        mock_get_instance.return_value = mock_instance

        response = client.get(
            "/api/v1/google-trends/trending-now-showcase-timeline"
            f"?keywords=python&timeframe={timeframe}"
        )

        assert response.status_code == 200, response.text
        assert "data" in response.json()

    # Test edge cases
    @patch('app.api.google_trends.google_trends_api.get_trends_instance')
    def test_empty_api_response_handling(self, mock_get_instance, client):
        """Test handling of empty API responses."""
        mock_instance = MagicMock()
        mock_instance.interest_over_time.return_value = None
        mock_get_instance.return_value = mock_instance

        response = client.get("/api/v1/google-trends/interest-over-time?keywords=python")

        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "No data returned from Google Trends."

    @patch('app.api.google_trends.google_trends_api.get_trends_instance')
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
        assert response.json()["detail"] == (
            "Upstream Google Trends returned an unusable response."
        )
        assert cache_manager_module._cache_store == {}
