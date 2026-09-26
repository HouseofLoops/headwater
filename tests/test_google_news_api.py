from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

# Assuming your FastAPI app and router are structured to be importable
# If gnews_router is in app.api.google_news.google_news_api, and you have a main app instance
# For simplicity, we might need to create a local app instance for testing the router
from app.api.google_news import google_news_api
from app.api.google_news.google_news_api import (
    NewsArticle,
    decode_and_process_articles,
    decode_google_news_url,
    gnews_router,
    is_google_news_redirect,
    transform_article,
)
from app.core import cache_manager as cache_manager_module
from app.services import google_news_article_service

# Setup a minimal FastAPI app for testing the router
app = FastAPI()
app.include_router(gnews_router, prefix="/news")
client = TestClient(app)


@pytest.fixture(autouse=True)
def clear_cache():
    """Give every test a clean process cache.

    Responses here are cached on the request parameters, so without this a
    test can silently read a value another test put there rather than
    exercising its own mock.
    """
    cache_manager_module._cache_store.clear()
    yield
    cache_manager_module._cache_store.clear()


class StubStream:
    """Stand-in for ``httpx.AsyncClient.stream``'s async context manager.

    Hands back pre-built responses in order and records the keyword arguments
    each call was made with, so tests can assert that redirects are not
    followed automatically.
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        outcome = self._responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return _StubStreamContext(outcome)


class _StubStreamContext:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *exc_info):
        return False


def html_response(body: str, url: str = "https://example.com/article"):
    return httpx.Response(200, html=body, request=httpx.Request("GET", url))


def redirect_response(location: str, url: str = "https://example.com/article"):
    return httpx.Response(302, headers={"location": location}, request=httpx.Request("GET", url))


@pytest.fixture
def stub_article_fetch(monkeypatch):
    """Serve /article-details/ canned HTTP responses instead of the network.

    The endpoint streams the page itself -- so it can re-validate redirects
    and cap the body size -- rather than letting newspaper fetch it, so the
    HTTP client is what tests stand in for. Returns a callable that installs
    the sequence of responses the client will yield.
    """
    mock_client = MagicMock()
    holder = {}

    def use(*responses):
        stream = StubStream(responses)
        mock_client.stream = stream
        holder["stream"] = stream
        return stream

    use(html_response("<html><body><p>Test article content</p></body></html>"))

    async def fake_get_client(proxy_url=None):
        return mock_client

    monkeypatch.setattr(google_news_article_service, "get_gnews_http_client", fake_get_client)
    use.calls = lambda: holder["stream"].calls
    return use


@pytest.fixture
def allow_example_articles(monkeypatch):
    """Allow-list example.com for /article-details/ without touching DNS.

    ``validate_outbound_url`` resolves the host and checks every address is
    globally routable; unit tests must not make network calls, so resolution
    is switched off here. The scheme, host, port and credential checks -- the
    ones these tests are about -- still run.
    """
    monkeypatch.setattr(google_news_article_service, "ARTICLE_DETAILS_ALLOWED_HOSTS", ("example.com", ".example.com"))
    monkeypatch.setattr(google_news_article_service, "ARTICLE_DETAILS_RESOLVE_DNS", False)
    monkeypatch.setattr(google_news_article_service, "ARTICLE_DETAILS_ALLOW_HTTP", False)


# 1. Test transform_article
def test_transform_article_success():
    raw_article = {
        "title": "Test Title",
        "description": "Test Description",
        "published date": "2023-01-01T12:00:00Z",
        "url": "http://example.com/article",
        "publisher": {"title": "Example Publisher"},
    }
    expected_transformed_article = {
        "title": "Test Title",
        "description": "Test Description",
        "published_date": "2023-01-01T12:00:00Z",
        "url": "http://example.com/article",
        "publisher": "Example Publisher",
    }
    assert transform_article(raw_article) == expected_transformed_article


def test_transform_article_missing_fields():
    raw_article = {
        "title": "Test Title Only",
        "url": "http://example.com/article_no_desc",
        # Missing description, published date, publisher
    }
    expected_transformed_article = {
        "title": "Test Title Only",
        "description": None,
        "published_date": None,
        "url": "http://example.com/article_no_desc",
        "publisher": None,
    }
    assert transform_article(raw_article) == expected_transformed_article


def test_transform_article_publisher_none():
    raw_article = {
        "title": "Test Title",
        "published date": "2023-01-01T12:00:00Z",
        "url": "http://example.com/article",
        "publisher": None,  # Publisher field exists but is None
    }
    expected_transformed_article = {
        "title": "Test Title",
        "description": None,
        "published_date": "2023-01-01T12:00:00Z",
        "url": "http://example.com/article",
        "publisher": None,
    }
    assert transform_article(raw_article) == expected_transformed_article


def test_transform_article_publisher_empty_dict():
    raw_article = {
        "title": "Test Title",
        "published date": "2023-01-01T12:00:00Z",
        "url": "http://example.com/article",
        "publisher": {},  # Publisher is an empty dict
    }
    expected_transformed_article = {
        "title": "Test Title",
        "description": None,
        "published_date": "2023-01-01T12:00:00Z",
        "url": "http://example.com/article",
        "publisher": None,
    }
    assert transform_article(raw_article) == expected_transformed_article


# 2. Test URL Decoding (decode_google_news_url)
@pytest.mark.asyncio
@patch("app.services.google_news_service.get_base64_str", new_callable=AsyncMock)
@patch("app.services.google_news_service.get_decoding_params", new_callable=AsyncMock)
@patch("app.services.google_news_service.decode_url", new_callable=AsyncMock)
async def test_decode_google_news_url_success(mock_decode_url, mock_get_decoding_params, mock_get_base64_str):
    mock_get_base64_str.return_value = {"status": True, "base64_str": "test_b64_str"}
    mock_get_decoding_params.return_value = {
        "status": True,
        "signature": "test_sig",
        "timestamp": "test_ts",
        "base64_str": "test_b64_str",
    }
    mock_decode_url.return_value = {"status": True, "decoded_url": "http://real-example.com"}

    source_url = "http://news.google.com/articles/test_b64_str"
    result = await decode_google_news_url(source_url)

    assert result == {"status": True, "decoded_url": "http://real-example.com"}
    mock_get_base64_str.assert_called_once_with(source_url)
    mock_get_decoding_params.assert_called_once_with("test_b64_str")
    mock_decode_url.assert_called_once_with("test_sig", "test_ts", "test_b64_str")


@pytest.mark.asyncio
@patch("app.services.google_news_service.get_base64_str", new_callable=AsyncMock)
async def test_decode_google_news_url_fail_base64(mock_get_base64_str):
    mock_get_base64_str.return_value = {"status": False, "message": "Invalid base64"}

    source_url = "http://invalid-google-news-url.com"
    result = await decode_google_news_url(source_url)

    assert result == {"status": False, "message": "Invalid base64"}
    mock_get_base64_str.assert_called_once_with(source_url)


@pytest.mark.asyncio
@patch("app.services.google_news_service.get_base64_str", new_callable=AsyncMock)
@patch("app.services.google_news_service.get_decoding_params", new_callable=AsyncMock)
async def test_decode_google_news_url_fail_decoding_params(mock_get_decoding_params, mock_get_base64_str):
    mock_get_base64_str.return_value = {"status": True, "base64_str": "test_b64_str"}
    mock_get_decoding_params.return_value = {"status": False, "message": "Failed to get params"}

    source_url = "http://news.google.com/articles/test_b64_str"
    result = await decode_google_news_url(source_url)

    assert result == {"status": False, "message": "Failed to get params"}
    mock_get_decoding_params.assert_called_once_with("test_b64_str")


@pytest.mark.asyncio
@patch("app.services.google_news_service.get_base64_str", new_callable=AsyncMock)
@patch("app.services.google_news_service.get_decoding_params", new_callable=AsyncMock)
@patch("app.services.google_news_service.decode_url", new_callable=AsyncMock)
async def test_decode_google_news_url_fail_decode_final_url(
    mock_decode_url, mock_get_decoding_params, mock_get_base64_str
):
    mock_get_base64_str.return_value = {"status": True, "base64_str": "test_b64_str"}
    mock_get_decoding_params.return_value = {
        "status": True,
        "signature": "test_sig",
        "timestamp": "test_ts",
        "base64_str": "test_b64_str",
    }
    mock_decode_url.return_value = {"status": False, "message": "Failed to decode URL"}

    source_url = "http://news.google.com/articles/test_b64_str"
    result = await decode_google_news_url(source_url)

    assert result == {"status": False, "message": "Failed to decode URL"}


# 3. Test an API Endpoint (e.g., /top/)
def test_get_top_google_news_success():
    # Mock GNews instance and its methods
    mock_gnews_instance = MagicMock()
    mock_gnews_instance.get_top_news.return_value = [
        {
            "title": "Top News 1",
            "url": "http://google.com/news/1",
            "publisher": {"title": "Pub1"},
            "published date": "date1",
            "description": "desc1",
        },
        {
            "title": "Top News 2",
            "url": "http://google.com/news/2",
            "publisher": {"title": "Pub2"},
            "published date": "date2",
            "description": "desc2",
        },
    ]

    expected_processed_articles = [
        NewsArticle(
            title="Top News 1",
            url="http://decoded.com/news/1",
            publisher="Pub1",
            published_date="date1",
            description="desc1",
        ).model_dump(),
        NewsArticle(
            title="Top News 2",
            url="http://decoded.com/news/2",
            publisher="Pub2",
            published_date="date2",
            description="desc2",
        ).model_dump(),
    ]

    # Patch get_gnews_instance and decode_and_process_articles
    with (
        patch("app.api.google_news.google_news_api.get_gnews_instance", new_callable=AsyncMock) as mock_get_gnews,
        patch(
            "app.api.google_news.google_news_api.decode_and_process_articles", new_callable=AsyncMock
        ) as mock_decode_process,
    ):
        mock_get_gnews.return_value = mock_gnews_instance
        mock_decode_process.return_value = (
            expected_processed_articles  # Simulate that articles were successfully decoded
        )

        response = client.get("/news/top/", params={"language": "en", "country": "US", "max_results": 2})

        assert response.status_code == 200
        response_json = response.json()
        assert response_json == {"articles": expected_processed_articles}

        # Assert that get_gnews_instance was called with correct parameters from the endpoint
        mock_get_gnews.assert_called_once()
        call_args = mock_get_gnews.call_args[1]  # Get kwargs
        assert call_args["language"] == "en"
        assert call_args["country"] == "US"
        assert call_args["max_results"] == 2

        # Assert that decode_and_process_articles was called with the raw articles from gnews
        mock_decode_process.assert_called_once_with(mock_gnews_instance.get_top_news.return_value)
        mock_gnews_instance.get_top_news.assert_called_once()


def test_get_top_google_news_gnews_returns_no_news():
    mock_gnews_instance = MagicMock()
    mock_gnews_instance.get_top_news.return_value = []  # GNews found no articles

    with patch("app.api.google_news.google_news_api.get_gnews_instance", new_callable=AsyncMock) as mock_get_gnews:
        mock_get_gnews.return_value = mock_gnews_instance

        response = client.get("/news/top/")

        assert response.status_code == 404
        assert response.json() == {"detail": "No top news found."}


def test_get_top_google_news_decode_returns_no_news():
    # Test case where GNews returns articles, but decode_and_process_articles returns empty
    mock_gnews_instance = MagicMock()
    mock_gnews_instance.get_top_news.return_value = [{"title": "Raw News 1", "url": "http://google.com/news/raw1"}]

    with (
        patch("app.api.google_news.google_news_api.get_gnews_instance", new_callable=AsyncMock) as mock_get_gnews,
        patch(
            "app.api.google_news.google_news_api.decode_and_process_articles", new_callable=AsyncMock
        ) as mock_decode_process,
    ):
        mock_get_gnews.return_value = mock_gnews_instance
        mock_decode_process.return_value = []  # Simulate all articles failed decoding or were filtered

        response = client.get("/news/top/")

        assert response.status_code == 404
        # This detail comes from the new check in the endpoint after calling decode_and_process_articles
        assert response.json() == {"detail": "No processable top news found after URL decoding."}
        mock_decode_process.assert_called_once_with(mock_gnews_instance.get_top_news.return_value)


# Additional comprehensive tests to increase coverage


# Test available languages endpoint
def test_get_languages():
    response = client.get("/news/available-languages/")
    assert response.status_code == 200
    data = response.json()
    assert "available_languages" in data
    assert isinstance(data["available_languages"], dict)
    assert "english" in data["available_languages"]


# Test available countries endpoint
def test_get_available_countries():
    response = client.get("/news/available-countries/")
    assert response.status_code == 200
    data = response.json()
    assert "available_countries" in data
    assert isinstance(data["available_countries"], dict)
    assert "United States" in data["available_countries"]


# Test /source/ endpoint
@pytest.mark.asyncio
@patch("app.api.google_news.google_news_api.get_gnews_instance", new_callable=AsyncMock)
@patch("app.api.google_news.google_news_api.decode_and_process_articles", new_callable=AsyncMock)
async def test_get_news_by_source_success(mock_decode_process, mock_get_gnews):
    mock_gnews_instance = MagicMock()
    mock_gnews_instance.get_news_by.return_value = [
        {"title": "Source News", "url": "http://google.com/news/source1", "publisher": {"title": "SourcePub"}}
    ]
    mock_get_gnews.return_value = mock_gnews_instance
    mock_decode_process.return_value = [
        NewsArticle(
            title="Source News",
            url="http://decoded.com/news/source1",
            publisher="SourcePub",
            published_date="2023-01-01",
            description="Test description",
        ).model_dump()
    ]

    response = client.get(
        "/news/source/", params={"source": "cnn.com", "language": "en", "country": "US", "max_results": 5}
    )

    assert response.status_code == 200
    data = response.json()
    assert "articles" in data
    assert len(data["articles"]) == 1


@pytest.mark.asyncio
async def test_get_news_by_source_invalid_source():
    # Test with invalid source
    response = client.get("/news/source/", params={"source": "invalid-source"})
    assert response.status_code == 400  # Validation error


# Test /search/ endpoint
@pytest.mark.asyncio
@patch("app.api.google_news.google_news_api.get_gnews_instance", new_callable=AsyncMock)
@patch("app.api.google_news.google_news_api.decode_and_process_articles", new_callable=AsyncMock)
async def test_search_google_news_success(mock_decode_process, mock_get_gnews):
    mock_gnews_instance = MagicMock()
    mock_gnews_instance.get_news.return_value = [
        {"title": "Search Result", "url": "http://google.com/news/search1", "publisher": {"title": "SearchPub"}}
    ]
    mock_get_gnews.return_value = mock_gnews_instance
    mock_decode_process.return_value = [
        NewsArticle(
            title="Search Result",
            url="http://decoded.com/news/search1",
            publisher="SearchPub",
            published_date="2023-01-01",
            description="Test description",
        ).model_dump()
    ]

    response = client.get(
        "/news/search/", params={"query": "test query", "language": "en", "country": "US", "max_results": 5}
    )

    assert response.status_code == 200
    data = response.json()
    assert "articles" in data


@pytest.mark.asyncio
@patch("app.api.google_news.google_news_api.get_gnews_instance", new_callable=AsyncMock)
async def test_search_google_news_no_results(mock_get_gnews):
    mock_gnews_instance = MagicMock()
    mock_gnews_instance.get_news.return_value = []
    mock_get_gnews.return_value = mock_gnews_instance

    response = client.get("/news/search/", params={"query": "nonexistent"})
    assert response.status_code == 404


# Test /topic/ endpoint
@pytest.mark.asyncio
@patch("app.api.google_news.google_news_api.get_gnews_instance", new_callable=AsyncMock)
@patch("app.api.google_news.google_news_api.decode_and_process_articles", new_callable=AsyncMock)
async def test_get_news_by_topic_success(mock_decode_process, mock_get_gnews):
    mock_gnews_instance = MagicMock()
    mock_gnews_instance.get_news_by_topic.return_value = [
        {"title": "Topic News", "url": "http://google.com/news/topic1", "publisher": {"title": "TopicPub"}}
    ]
    mock_get_gnews.return_value = mock_gnews_instance
    mock_decode_process.return_value = [
        NewsArticle(
            title="Topic News",
            url="http://decoded.com/news/topic1",
            publisher="TopicPub",
            published_date="2023-01-01",
            description="Test description",
        ).model_dump()
    ]

    response = client.get(
        "/news/topic/", params={"topic": "WORLD", "language": "en", "country": "US", "max_results": 5}
    )

    assert response.status_code == 200
    data = response.json()
    assert "articles" in data


# Test /location/ endpoint
@pytest.mark.asyncio
@patch("app.api.google_news.google_news_api.get_gnews_instance", new_callable=AsyncMock)
@patch("app.api.google_news.google_news_api.decode_and_process_articles", new_callable=AsyncMock)
async def test_get_news_by_location_success(mock_decode_process, mock_get_gnews):
    mock_gnews_instance = MagicMock()
    mock_gnews_instance.get_news_by_location.return_value = [
        {"title": "Location News", "url": "http://google.com/news/location1", "publisher": {"title": "LocationPub"}}
    ]
    mock_get_gnews.return_value = mock_gnews_instance
    mock_decode_process.return_value = [
        NewsArticle(
            title="Location News",
            url="http://decoded.com/news/location1",
            publisher="LocationPub",
            published_date="2023-01-01",
            description="Test description",
        ).model_dump()
    ]

    response = client.get(
        "/news/location/", params={"location": "New York", "language": "en", "country": "US", "max_results": 5}
    )

    assert response.status_code == 200
    data = response.json()
    assert "articles" in data


# Test /articles/ endpoint
@pytest.mark.asyncio
@patch("app.api.google_news.google_news_api.get_gnews_instance", new_callable=AsyncMock)
@patch("app.api.google_news.google_news_api.decode_and_process_articles", new_callable=AsyncMock)
async def test_get_google_news_articles_success(mock_decode_process, mock_get_gnews):
    mock_gnews_instance = MagicMock()
    mock_gnews_instance.get_news.return_value = [
        {"title": "Bulk Article", "url": "http://google.com/news/bulk1", "publisher": {"title": "BulkPub"}}
    ]
    mock_get_gnews.return_value = mock_gnews_instance
    mock_decode_process.return_value = [
        NewsArticle(
            title="Bulk Article",
            url="http://decoded.com/news/bulk1",
            publisher="BulkPub",
            published_date="2023-01-01",
            description="Test description",
        ).model_dump()
    ]

    response = client.get(
        "/news/articles/", params={"query": "test", "country": "US", "language": "en", "period": "7d", "max_results": 5}
    )

    assert response.status_code == 200
    data = response.json()
    assert "articles" in data


# Test /article-details/ endpoint
@pytest.mark.asyncio
@patch("app.api.google_news.google_news_api.Article")
async def test_get_article_details_success(mock_article_class, allow_example_articles, stub_article_fetch):
    # Create a mock article instance
    mock_article = MagicMock()
    mock_article.title = "Test Article Title"
    mock_article.text = "Test article content"
    mock_article.summary = "Test summary"
    mock_article.authors = ["Test Author"]
    mock_article.publish_date = "2023-01-01"
    mock_article.top_image = "http://example.com/image.jpg"
    mock_article.keywords = ["test", "article"]
    mock_article.images = set()
    mock_article.movies = []
    mock_article.meta_data = {}
    mock_article.meta_description = ""
    mock_article.meta_keywords = ""

    # Mock the synchronous methods
    mock_article.download = MagicMock()
    mock_article.parse = MagicMock()
    mock_article.nlp = MagicMock()

    # Make the Article constructor return our mock
    mock_article_class.return_value = mock_article

    # https, and on the allow-list: the only kind of URL this endpoint fetches.
    response = client.get("/news/article-details/", params={"url": "https://example.com/article"})

    assert response.status_code == 200
    data = response.json()
    assert "title" in data
    assert "text" in data
    assert data["title"] == "Test Article Title"
    # The validated, normalised URL is what gets fetched.
    assert mock_article_class.call_args[0][0] == "https://example.com/article"


# Test helper functions
@pytest.mark.asyncio
@patch("app.services.google_news_service.cache_manager")
@patch("app.services.google_news_service.settings")
async def test_get_cached_or_fetch_cache_hit(mock_settings, mock_cache_manager):
    mock_settings.ENABLE_CACHE = True
    mock_cache_manager.get = AsyncMock(return_value={"cached": "data"})

    from app.api.google_news.google_news_api import get_cached_or_fetch

    async def dummy_fetch():
        return {"fresh": "data"}

    result = await get_cached_or_fetch("test_key", dummy_fetch)
    assert result == {"cached": "data"}
    mock_cache_manager.get.assert_called_once_with("test_key", namespace="gnews")


@pytest.mark.asyncio
@patch("app.services.google_news_service.cache_manager")
@patch("app.services.google_news_service.settings")
async def test_get_cached_or_fetch_cache_miss(mock_settings, mock_cache_manager):
    mock_settings.ENABLE_CACHE = True
    mock_cache_manager.get = AsyncMock(return_value=None)
    mock_cache_manager.set = AsyncMock()

    from app.api.google_news.google_news_api import get_cached_or_fetch

    async def dummy_fetch():
        return {"fresh": "data"}

    result = await get_cached_or_fetch("test_key", dummy_fetch)
    assert result == {"fresh": "data"}
    mock_cache_manager.set.assert_called_once()


@pytest.mark.asyncio
@patch("app.services.google_news_service.settings")
async def test_get_cached_or_fetch_cache_disabled(mock_settings):
    mock_settings.ENABLE_CACHE = False

    from app.api.google_news.google_news_api import get_cached_or_fetch

    async def dummy_fetch():
        return {"fresh": "data"}

    result = await get_cached_or_fetch("test_key", dummy_fetch)
    assert result == {"fresh": "data"}


# Test generate_cache_key function
def test_generate_cache_key():
    from app.api.google_news.google_news_api import generate_cache_key

    key = generate_cache_key("test_endpoint", param1="value1", param2="value2")
    assert "gnews:test_endpoint:" in key
    assert "param1:value1" in key
    assert "param2:value2" in key


# Test SourceQuery model validation
def test_source_query_valid():
    from app.api.google_news.google_news_api import SourceQuery

    source = SourceQuery(source="cnn.com")
    assert source.source == "cnn.com"


def test_source_query_invalid():
    from app.api.google_news.google_news_api import SourceQuery

    with pytest.raises(ValidationError):
        SourceQuery(source="invalid-source")


# Test NewsArticle model
def test_news_article_model():
    article = NewsArticle(
        title="Test Title",
        url="http://example.com",
        publisher="Test Publisher",
        published_date="2023-01-01",
        description="Test description",
    )
    assert article.title == "Test Title"
    assert article.url == "http://example.com"


# Test error responses
@pytest.mark.asyncio
@patch("app.api.google_news.google_news_api.get_gnews_instance", new_callable=AsyncMock)
async def test_get_top_google_news_internal_error(mock_get_gnews):
    mock_get_gnews.side_effect = Exception("Internal error")

    response = client.get("/news/top/")
    assert response.status_code == 500
    assert "Internal Server Error" in response.json()["detail"]


# Test parameter validation
def test_search_parameters_validation():
    # Test max_results bounds
    response = client.get("/news/search/", params={"query": "test", "max_results": 150})
    assert response.status_code == 422  # Should fail validation

    response = client.get("/news/search/", params={"query": "test", "max_results": 0})
    assert response.status_code == 422  # Should fail validation


# Test date parameter validation
def test_date_parameter_validation():
    response = client.get("/news/search/", params={"query": "test", "start_date": "invalid-date"})
    assert response.status_code == 422


# Test sort_by parameter validation
def test_sort_by_parameter_validation():
    response = client.get("/news/search/", params={"query": "test", "sort_by": "invalid_sort"})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# CRT-3: /article-details/ was a server-side request forgery sink
#
# It fetched any URL the caller supplied, from inside the container network,
# and the 503 body named the target host and the failure cause -- which made
# it usable as an internal port scanner. These tests pin both halves of the
# fix: the request is refused, and every refusal looks identical.
# ---------------------------------------------------------------------------

BLOCKED_URLS = [
    pytest.param("http://127.0.0.1:8000/admin", id="loopback"),
    pytest.param("http://169.254.169.254/latest/meta-data/", id="cloud-metadata"),
    pytest.param("http://[::1]:6379/", id="ipv6-loopback"),
    pytest.param("http://10.0.0.5:5432/", id="rfc1918"),
    pytest.param("https://redis.internal:6379/", id="internal-service-name"),
    pytest.param("file:///etc/passwd", id="file-scheme"),
    pytest.param("gopher://127.0.0.1:11211/", id="gopher-scheme"),
    pytest.param("https://example.com@evil.test/", id="credential-smuggling"),
    pytest.param("https://example.com.evil.test/", id="suffix-confusion"),
    pytest.param("https://evil.test/article", id="host-not-allow-listed"),
    pytest.param("https://example.com:8080/article", id="non-standard-port"),
    pytest.param("http://example.com/article", id="plaintext-http"),
    pytest.param("/etc/passwd", id="bare-path"),
    pytest.param("//example.com/article", id="scheme-relative"),
]


@pytest.mark.parametrize("url", BLOCKED_URLS)
@patch("app.api.google_news.google_news_api.Article")
def test_article_details_blocks_ssrf_targets(mock_article_class, allow_example_articles, url):
    """A blocked URL is refused before anything is fetched."""
    response = client.get("/news/article-details/", params={"url": url})

    assert response.status_code == 400
    # Nothing was fetched: validation happens before the download.
    mock_article_class.assert_not_called()


@pytest.mark.parametrize("url", BLOCKED_URLS)
def test_article_details_refusals_are_indistinguishable(allow_example_articles, url):
    """Every refusal returns the same status and the same body.

    This is the property that removes the port-scan oracle. If "connection
    refused" read differently from "not on the allow-list", a caller could map
    the internal network by diffing responses, so the reason is logged and
    never returned.
    """
    response = client.get("/news/article-details/", params={"url": url})

    assert response.status_code == 400
    assert response.json() == {"detail": "The supplied URL is not permitted."}


def test_article_details_does_not_leak_upstream_failure(allow_example_articles, stub_article_fetch):
    """A fetch failure must not describe itself to the caller.

    The old handler returned 503 with the exception text, which named the host
    and said why the connection failed -- so a caller could tell "port closed"
    from "port open but not HTML" and map the internal network.
    """
    stub_article_fetch(httpx.ConnectError("Connection refused to 10.0.0.5:6379"))

    response = client.get("/news/article-details/", params={"url": "https://example.com/article"})

    assert response.status_code == 502
    assert response.json() == {"detail": "Could not retrieve the requested article."}
    assert "10.0.0.5" not in response.text
    assert "refused" not in response.text.lower()


def _stub_parsed_article(title="Parsed"):
    article = MagicMock()
    article.title = title
    article.text = "Body text"
    article.authors = []
    article.publish_date = None
    article.top_image = ""
    article.images = set()
    article.movies = []
    article.meta_data = {}
    article.meta_description = ""
    article.meta_keywords = ""
    article.summary = ""
    article.keywords = []
    return article


@pytest.mark.parametrize(
    "location",
    [
        pytest.param("http://169.254.169.254/latest/meta-data/", id="absolute"),
        pytest.param("http://127.0.0.1:6379/", id="loopback"),
        pytest.param("file:///etc/passwd", id="scheme-change"),
    ],
)
@patch("app.api.google_news.google_news_api.Article")
def test_article_details_does_not_follow_redirect_off_the_allow_list(
    mock_article_class, allow_example_articles, stub_article_fetch, location
):
    """A redirect is a second request and gets the same validation.

    Validating once and handing the URL to a library that follows redirects
    checks only the first hop: an allow-listed host answering
    ``302 -> http://169.254.169.254/`` would send the library somewhere the
    guard never saw.
    """
    stub_article_fetch(redirect_response(location))

    response = client.get("/news/article-details/", params={"url": "https://example.com/article"})

    # The redirect target was refused, so nothing was ever parsed.
    assert response.status_code == 502
    assert response.json() == {"detail": "Could not retrieve the requested article."}
    mock_article_class.assert_not_called()

    calls = stub_article_fetch.calls()
    # Exactly one outbound request: the redirect was not followed.
    assert len(calls) == 1
    assert calls[0][2]["follow_redirects"] is False


@patch("app.api.google_news.google_news_api.Article")
def test_article_details_follows_an_allow_listed_redirect(
    mock_article_class, allow_example_articles, stub_article_fetch
):
    """A redirect that stays on the allow-list is followed and parsed."""
    stub_article_fetch(
        redirect_response("/final"),  # relative: resolves to example.com
        html_response("<html><body><p>Body text</p></body></html>"),
    )
    mock_article_class.return_value = _stub_parsed_article("Redirected")

    response = client.get("/news/article-details/", params={"url": "https://example.com/article"})

    assert response.status_code == 200
    assert response.json()["title"] == "Redirected"
    # Newspaper parsed the body we fetched; it made no request of its own.
    mock_article_class.return_value.download.assert_called_once_with("<html><body><p>Body text</p></body></html>")
    assert mock_article_class.call_args[0][0] == "https://example.com/final"


@patch("app.api.google_news.google_news_api.Article")
def test_article_details_bounds_the_redirect_chain(mock_article_class, allow_example_articles, stub_article_fetch):
    """A redirect loop must not keep a worker busy indefinitely."""
    stub_article_fetch(*[redirect_response("/next") for _ in range(10)])

    response = client.get("/news/article-details/", params={"url": "https://example.com/article"})

    assert response.status_code == 502
    mock_article_class.assert_not_called()
    assert len(stub_article_fetch.calls()) == google_news_api.ARTICLE_MAX_REDIRECTS + 1


@patch("app.api.google_news.google_news_api.Article")
def test_article_details_caps_the_response_body(
    mock_article_class, allow_example_articles, stub_article_fetch, monkeypatch
):
    """An oversized body is abandoned rather than buffered.

    The cap has to apply to the decompressed stream: a few kilobytes of gzip
    can expand to gigabytes, so a Content-Length check would not do.
    """
    monkeypatch.setattr(google_news_article_service, "ARTICLE_MAX_BYTES", 1024)
    stub_article_fetch(html_response("x" * 5000))

    response = client.get("/news/article-details/", params={"url": "https://example.com/article"})

    assert response.status_code == 502
    assert response.json() == {"detail": "Could not retrieve the requested article."}
    mock_article_class.assert_not_called()


@patch("app.api.google_news.google_news_api.Article")
def test_degraded_nlp_result_is_not_cached(mock_article_class, allow_example_articles, stub_article_fetch):
    """A TRANSIENTLY degraded response must not be pinned for an hour.

    ``article.nlp()`` raises LookupError when nltk is installed but its punkt
    corpus is not. That resolves the moment the corpus lands, so the endpoint
    answers 200 with a summary-less article and the result must not be cached,
    or callers keep getting the degraded version long afterwards.

    The other degraded state is deliberately not covered here: when nltk is not
    installed at all, newspaper4k raises ImportError instead, and that IS
    cached, because it is a steady state rather than a gap that will close. See
    test_missing_nltk_result_is_cached.
    """
    degraded = _stub_parsed_article("No NLP")
    degraded.nlp.side_effect = LookupError("punkt not found")
    mock_article_class.return_value = degraded

    first = client.get("/news/article-details/", params={"url": "https://example.com/article"})

    assert first.status_code == 200
    # `error` is what is_cacheable() keys on, so it must survive: it is the
    # marker that keeps a transiently-degraded article out of the cache.
    assert "error" in first.json()
    # The NLP fields are now present and null rather than absent, so a caller
    # reading response["summary"] gets None instead of a KeyError. Nothing
    # branches on their presence - is_cacheable() looks only at `partial` and
    # `error` - so this is a response-shape choice, not a behavioural one.
    assert first.json()["summary"] is None
    assert first.json()["keywords"] is None
    assert first.json()["nlp_available"] is False
    assert cache_manager_module._cache_store == {}

    # NLTK becomes available; the next request must get the full article.
    healthy = _stub_parsed_article("With NLP")
    healthy.summary = "A summary"
    healthy.keywords = ["a"]
    mock_article_class.return_value = healthy
    stub_article_fetch(html_response("<html><body><p>Body text</p></body></html>"))

    second = client.get("/news/article-details/", params={"url": "https://example.com/article"})

    assert second.json()["summary"] == "A summary"
    assert "error" not in second.json()
    # A complete result is worth keeping.
    assert cache_manager_module._cache_store != {}


@patch("app.api.google_news.google_news_api.Article")
def test_missing_nltk_result_is_cached(mock_article_class, allow_example_articles, stub_article_fetch):
    """No nltk at all is a steady state, so the article is still worth caching.

    newspaper4k raises ImportError from split_sentences() when nltk is absent.
    Unlike a missing corpus, that does not resolve on its own - nltk is not a
    dependency while PYSEC-2026-3740 is unfixed - so refusing to cache it would
    disable caching on this endpoint permanently, for a gap that is never
    going to close.
    """
    degraded = _stub_parsed_article("No nltk")
    degraded.nlp.side_effect = ImportError("nltk is required for NLP features.")
    mock_article_class.return_value = degraded

    response = client.get("/news/article-details/", params={"url": "https://example.com/article"})

    assert response.status_code == 200
    assert response.json()["summary"] is None
    assert response.json()["keywords"] is None
    assert response.json()["nlp_available"] is False
    # No `error` key: that is what makes it cacheable.
    assert "error" not in response.json()
    assert cache_manager_module._cache_store != {}


def test_article_details_default_allow_list_is_closed():
    """With no operator configuration, only Google News is reachable.

    An empty allow-list would make ``validate_outbound_url`` refuse
    everything, and a wildcard one would restore the vulnerability; the
    default sits deliberately at "the one host this service already talks to".
    """
    assert google_news_api.ARTICLE_DETAILS_ALLOWED_HOSTS == (
        "news.google.com",
        ".news.google.com",
    )
    assert google_news_api.ARTICLE_DETAILS_ALLOW_HTTP is False


# ---------------------------------------------------------------------------
# CRT-9: every Google News endpoint answered 404
#
# gnews >= 0.5 resolves article links itself, so article["url"] is usually the
# publisher's URL. The pipeline required the news.google.com/rss/articles/...
# form and threw away everything else, emptying the list on every request.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://news.google.com/rss/articles/CBMiK2h0", True),
        ("https://news.google.com/read/CBMiK2h0", True),
        ("https://NEWS.GOOGLE.COM/rss/articles/CBMiK2h0", True),
        ("https://www.cnn.com/2026/01/01/politics/story/index.html", False),
        ("https://news.google.com.evil.test/rss/articles/x", False),
        ("", False),
    ],
)
def test_is_google_news_redirect(url, expected):
    """Only genuine Google News redirects need the decode round-trip."""
    assert is_google_news_redirect(url) is expected


@pytest.mark.asyncio
async def test_pre_resolved_urls_are_kept():
    """A publisher URL from gnews is used as-is, not discarded.

    Before the fix this returned an empty list -- every article was rejected
    for not matching the Google News redirect shape -- and the endpoints
    turned that into 404 on every single request.
    """
    raw_articles = [
        {
            "title": "Resolved Article",
            "description": "desc",
            "published date": "2026-01-01",
            "url": "https://www.cnn.com/2026/01/01/story/index.html",
            "publisher": {"title": "CNN"},
        }
    ]

    with patch(
        "app.services.google_news_service.decode_google_news_url",
        new_callable=AsyncMock,
    ) as mock_decode:
        result = await decode_and_process_articles(raw_articles)

    assert len(result) == 1
    assert result[0]["url"] == "https://www.cnn.com/2026/01/01/story/index.html"
    assert result.failed == 0
    # No decode round-trip for an already-resolved URL.
    mock_decode.assert_not_called()


@pytest.mark.asyncio
async def test_redirect_urls_are_still_decoded():
    """The old redirect form still goes through the decoder."""
    raw_articles = [
        {
            "title": "Redirect Article",
            "url": "https://news.google.com/rss/articles/CBMiK2h0",
            "publisher": {"title": "CNN"},
        }
    ]

    with patch(
        "app.services.google_news_service.decode_google_news_url",
        new_callable=AsyncMock,
    ) as mock_decode:
        mock_decode.return_value = {
            "status": True,
            "decoded_url": "https://www.cnn.com/real/story",
        }
        result = await decode_and_process_articles(raw_articles)

    assert [article["url"] for article in result] == ["https://www.cnn.com/real/story"]
    assert result.failed == 0


@pytest.mark.asyncio
async def test_failed_articles_are_counted_not_silently_dropped():
    """Articles lost to a decode failure are counted, not quietly forgotten."""
    raw_articles = [
        {"title": "Good", "url": "https://www.cnn.com/story"},
        {"title": "Bad", "url": "https://news.google.com/rss/articles/xyz"},
        {"title": "No URL"},
    ]

    with patch(
        "app.services.google_news_service.decode_google_news_url",
        new_callable=AsyncMock,
    ) as mock_decode:
        mock_decode.return_value = {"status": False, "message": "boom"}
        result = await decode_and_process_articles(raw_articles)

    assert len(result) == 1
    assert result.failed == 2
    assert result.partial is True


@pytest.mark.asyncio
async def test_domain_filtering_is_not_counted_as_failure():
    """Filtering an article out on purpose is not a failure."""
    raw_articles = [
        {"title": "CNN", "url": "https://www.cnn.com/story"},
        {"title": "BBC", "url": "https://www.bbc.co.uk/story"},
    ]

    result = await decode_and_process_articles(raw_articles, filter_by_domain="cnn.com")

    assert len(result) == 1
    assert result.failed == 0
    assert result.filtered_out == 1
    assert result.partial is False


def test_partial_results_are_flagged_to_the_caller():
    """A shortened list must say it is short.

    Silently returning one of two articles looks identical to Google News only
    having one, which is how a broken parser hides.
    """
    from app.api.google_news.google_news_api import ProcessedArticles

    mock_gnews_instance = MagicMock()
    mock_gnews_instance.get_top_news.return_value = [{"title": "a"}, {"title": "b"}]

    survivors = ProcessedArticles(
        [
            NewsArticle(
                title="Top News 1",
                url="http://decoded.com/news/1",
                publisher="Pub1",
                published_date="date1",
                description="desc1",
            ).model_dump()
        ],
        total=2,
        failed=1,
    )

    with (
        patch("app.api.google_news.google_news_api.get_gnews_instance", new_callable=AsyncMock) as mock_get_gnews,
        patch(
            "app.api.google_news.google_news_api.decode_and_process_articles", new_callable=AsyncMock
        ) as mock_decode_process,
    ):
        mock_get_gnews.return_value = mock_gnews_instance
        mock_decode_process.return_value = survivors

        response = client.get("/news/top/")

    assert response.status_code == 200
    body = response.json()
    assert body["partial"] is True
    assert body["dropped"] == 1
    assert len(body["articles"]) == 1


def test_total_decode_failure_is_502_not_404():
    """Losing every article to failures is an upstream problem, not "no news".

    404 tells a client "there is nothing here, stop asking". When the truth is
    "our resolver broke", that is a 502 the client can retry and monitoring
    can alert on.
    """
    from app.api.google_news.google_news_api import ProcessedArticles

    mock_gnews_instance = MagicMock()
    mock_gnews_instance.get_top_news.return_value = [{"title": "a"}, {"title": "b"}]

    with (
        patch("app.api.google_news.google_news_api.get_gnews_instance", new_callable=AsyncMock) as mock_get_gnews,
        patch(
            "app.api.google_news.google_news_api.decode_and_process_articles", new_callable=AsyncMock
        ) as mock_decode_process,
    ):
        mock_get_gnews.return_value = mock_gnews_instance
        mock_decode_process.return_value = ProcessedArticles([], total=2, failed=2)

        response = client.get("/news/top/")

    assert response.status_code == 502
    assert response.json() == {"detail": "Could not resolve articles from Google News. Please retry."}


def test_partial_results_are_not_cached():
    """A partial result must not be pinned for the whole TTL.

    Caching one is the quieter version of caching a failure: the short list
    gets served to everyone for ten minutes, and nothing about the response
    says it came from a bad moment rather than from Google News.
    """
    from app.api.google_news.google_news_api import ProcessedArticles

    def article(title):
        return NewsArticle(
            title=title,
            url=f"http://decoded.com/{title}",
            publisher="Pub",
            published_date="date",
            description="desc",
        ).model_dump()

    mock_gnews_instance = MagicMock()
    mock_gnews_instance.get_top_news.return_value = [{"title": "a"}, {"title": "b"}]

    with (
        patch("app.api.google_news.google_news_api.get_gnews_instance", new_callable=AsyncMock) as mock_get_gnews,
        patch(
            "app.api.google_news.google_news_api.decode_and_process_articles", new_callable=AsyncMock
        ) as mock_decode_process,
    ):
        mock_get_gnews.return_value = mock_gnews_instance
        mock_decode_process.return_value = ProcessedArticles([article("one")], total=2, failed=1)

        first = client.get("/news/top/")
        assert first.status_code == 200
        assert first.json()["partial"] is True
        assert cache_manager_module._cache_store == {}

        # The transient failure clears: the next request sees both articles.
        mock_decode_process.return_value = ProcessedArticles([article("one"), article("two")], total=2, failed=0)
        second = client.get("/news/top/")

    assert len(second.json()["articles"]) == 2
    assert "partial" not in second.json()
    # A complete result is worth keeping.
    assert cache_manager_module._cache_store != {}


def test_failed_news_response_is_not_cached():
    """A 502 must not be written to the cache."""
    from app.api.google_news.google_news_api import ProcessedArticles

    mock_gnews_instance = MagicMock()
    mock_gnews_instance.get_top_news.return_value = [{"title": "a"}]

    with (
        patch("app.api.google_news.google_news_api.get_gnews_instance", new_callable=AsyncMock) as mock_get_gnews,
        patch(
            "app.api.google_news.google_news_api.decode_and_process_articles", new_callable=AsyncMock
        ) as mock_decode_process,
    ):
        mock_get_gnews.return_value = mock_gnews_instance
        mock_decode_process.return_value = ProcessedArticles([], total=1, failed=1)

        assert client.get("/news/top/").status_code == 502
        assert cache_manager_module._cache_store == {}

        # Upstream recovers: the next request must not be served the failure.
        mock_decode_process.return_value = ProcessedArticles(
            [
                NewsArticle(
                    title="Top News 1",
                    url="http://decoded.com/news/1",
                    publisher="Pub1",
                    published_date="date1",
                    description="desc1",
                ).model_dump()
            ],
            total=1,
            failed=0,
        )
        recovered = client.get("/news/top/")

    assert recovered.status_code == 200
    assert len(recovered.json()["articles"]) == 1


# -----------------------------------------------------------------------------
# Google News rate limiting -> 429, not 500
# -----------------------------------------------------------------------------
def _rate_limited_gnews():
    """A real GNews whose feed fetch always answers 429.

    gnews retries the 429 with backoff (``_sleep`` is its test seam), then
    raises its own ``RateLimitError``, which the route catch-alls used to
    report as 500 Internal Server Error.
    """
    from types import SimpleNamespace

    from gnews import GNews

    gnews = GNews(language="en", country="US", max_results=5)
    gnews._fetch_feed = lambda url: SimpleNamespace(status=429, entries=[])
    gnews._sleep = lambda seconds: None
    return gnews


@pytest.fixture
def problem_news_client():
    """The News router behind Headwater's real RFC 7807 exception handlers."""
    from app.core.exceptions import configure_exception_handlers
    from app.core.rate_limiter import rate_limit

    problem_app = FastAPI()
    configure_exception_handlers(problem_app)
    problem_app.include_router(gnews_router, prefix="/news")
    problem_app.dependency_overrides[rate_limit] = lambda: None
    return TestClient(problem_app)


@pytest.mark.parametrize(
    "path",
    [
        "/news/search/?query=python",
        "/news/top/",
        "/news/topic/?topic=TECHNOLOGY",
        "/news/location/?location=London",
        "/news/source/?source=cnn.com",
        "/news/articles/?query=python",
    ],
)
def test_google_news_rate_limit_is_429_with_retry_after(problem_news_client, override_settings, path):
    override_settings(UPSTREAM_RETRY_AFTER_SECONDS=33)

    with patch(
        "app.api.google_news.google_news_api.get_gnews_instance",
        new_callable=AsyncMock,
        return_value=_rate_limited_gnews(),
    ):
        response = problem_news_client.get(path)

    assert response.status_code == 429, response.text
    assert response.headers["Retry-After"] == "33"
    assert response.headers["Content-Type"].startswith("application/problem+json")
    body = response.json()
    assert body["type"] == "https://headwater.com/problems/upstream_rate_limited"
    assert body["upstream"] == "Google News"
    assert cache_manager_module._cache_store == {}
