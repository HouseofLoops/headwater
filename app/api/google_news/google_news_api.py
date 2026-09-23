import asyncio
import hashlib
import logging
import re
from urllib.parse import quote, urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request  # Ensure Depends is imported if not already
from fastapi.responses import JSONResponse
from newspaper import Article, ArticleException, Config
from pydantic import BaseModel, ValidationError, validator

from app.core.rate_limiter import rate_limit
from app.core.url_guard import UrlNotAllowed
from app.services.google_news_article_service import (
    ARTICLE_DETAILS_ALLOW_HTTP as ARTICLE_DETAILS_ALLOW_HTTP,
)
from app.services.google_news_article_service import (
    ARTICLE_DETAILS_ALLOWED_HOSTS as ARTICLE_DETAILS_ALLOWED_HOSTS,
)
from app.services.google_news_article_service import (
    ARTICLE_DETAILS_RESOLVE_DNS as ARTICLE_DETAILS_RESOLVE_DNS,
)
from app.services.google_news_article_service import (
    ARTICLE_FETCH_FAILED_DETAIL as ARTICLE_FETCH_FAILED_DETAIL,
)
from app.services.google_news_article_service import (
    ARTICLE_MAX_BYTES as ARTICLE_MAX_BYTES,
)
from app.services.google_news_article_service import (
    ARTICLE_MAX_REDIRECTS as ARTICLE_MAX_REDIRECTS,
)
from app.services.google_news_article_service import (
    BLOCKED_URL_DETAIL as BLOCKED_URL_DETAIL,
)
from app.services.google_news_article_service import (
    _configured_article_hosts as _configured_article_hosts,
)
from app.services.google_news_article_service import (
    ensure_nltk_setup as ensure_nltk_setup,
)
from app.services.google_news_article_service import (
    fetch_allow_listed_html as fetch_allow_listed_html,
)
from app.services.google_news_article_service import (
    nltk as nltk,
)
from app.services.google_news_article_service import (
    setup_nltk as setup_nltk,
)
from app.services.google_news_article_service import (
    validate_article_url as validate_article_url,
)
from app.services.google_news_catalog import (
    AVAILABLE_COUNTRIES as AVAILABLE_COUNTRIES,
)
from app.services.google_news_catalog import (
    AVAILABLE_LANGUAGES as AVAILABLE_LANGUAGES,
)

# Non-route logic lives in the service layer. Every name is re-exported here
# (explicit `as` form) so existing `from ...google_news_api import X` imports
# keep working. Module state -- the settings object, the nltk setup task, the
# allow-list constants -- is defined once, in the service module that uses it.
#
# Patching: a re-exported name here is only a second reference. The route
# handlers below look up get_gnews_instance, decode_and_process_articles,
# Article, etc. in THIS module, so patch those here; anything the services look
# up (decode_url, settings, cache_manager, ARTICLE_* limits,
# get_gnews_http_client for the article fetch) must be patched on the service
# module that defines or uses it.
from app.services.google_news_catalog import (
    AVAILABLE_TOPICS as AVAILABLE_TOPICS,
)
from app.services.google_news_service import (
    CACHE_NAMESPACE as CACHE_NAMESPACE,
)
from app.services.google_news_service import (
    GOOGLE_NEWS_HOSTS as GOOGLE_NEWS_HOSTS,
)
from app.services.google_news_service import (
    UPSTREAM_NEWS_FAILURE_DETAIL as UPSTREAM_NEWS_FAILURE_DETAIL,
)
from app.services.google_news_service import (
    ProcessedArticles as ProcessedArticles,
)
from app.services.google_news_service import (
    build_news_response as build_news_response,
)
from app.services.google_news_service import (
    decode_and_process_articles as decode_and_process_articles,
)
from app.services.google_news_service import (
    decode_google_news_url as decode_google_news_url,
)
from app.services.google_news_service import (
    decode_url as decode_url,
)
from app.services.google_news_service import (
    generate_cache_key as generate_cache_key,
)
from app.services.google_news_service import (
    get_base64_str as get_base64_str,
)
from app.services.google_news_service import (
    get_cached_or_fetch as get_cached_or_fetch,
)
from app.services.google_news_service import (
    get_decoding_params as get_decoding_params,
)
from app.services.google_news_service import (
    get_gnews_http_client as get_gnews_http_client,
)
from app.services.google_news_service import (
    get_gnews_instance as get_gnews_instance,
)
from app.services.google_news_service import (
    is_cacheable as is_cacheable,
)
from app.services.google_news_service import (
    is_google_news_redirect as is_google_news_redirect,
)
from app.services.google_news_service import (
    settings as settings,
)
from app.services.google_news_service import (
    transform_article as transform_article,
)
from app.services.google_news_service import (
    validate_date_format as validate_date_format,
)

# Initialize Google News API Router
gnews_router = APIRouter()
logger = logging.getLogger(__name__)
# logging.basicConfig(level=logging.DEBUG)  # Ensure DEBUG level logs are captured -> This should be handled by the main application entry point


# Pydantic Model for Input Validation
class SourceQuery(BaseModel):
    source: str

    @validator("source")
    def validate_source(cls, v):
        # Optimized regex to validate domain names or full URLs
        pattern = r"^(https?://)?(www\.)?([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}$"
        if not re.fullmatch(pattern, v):
            raise ValueError("Invalid source URL or domain.")
        return v


# -----------------------------------------------------------------------------
# Pydantic Models for Responses
# -----------------------------------------------------------------------------
class NewsArticle(BaseModel):
    title: str
    published_date: str
    description: str | None
    url: str
    publisher: str | None


class NewsResponse(BaseModel):
    """A list of articles, plus a signal when some were lost.

    ``partial`` and ``dropped`` are omitted (via
    ``response_model_exclude_none``) on a clean response, so a complete result
    is exactly ``{"articles": [...]}`` as before. When articles were dropped,
    the caller is told rather than being handed a short list that looks whole.
    """

    articles: list[NewsArticle]
    partial: bool | None = None
    dropped: int | None = None


class ErrorResponse(BaseModel):
    detail: str


# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------


@gnews_router.get("/available-languages/", summary="Available Languages", response_model=dict)
async def get_languages(
    # === AUTH ===
    rate_limit_check: None = Depends(rate_limit),
):
    """Get supported languages for Google News."""
    return {"available_languages": AVAILABLE_LANGUAGES}


@gnews_router.get("/available-countries/", summary="Available Countries", response_model=dict)
async def get_available_countries(
    # === AUTH ===
    rate_limit_check: None = Depends(rate_limit),
):
    """Get supported countries for Google News."""
    return {"available_countries": AVAILABLE_COUNTRIES}


@gnews_router.get("/source/", summary="News by Source", response_model=NewsResponse, response_model_exclude_none=True)
async def get_news_by_source(
    # === REQUIRED ===
    source: str = Query(..., description="Source domain or URL", examples=["cnn.com"]),
    # === COMMONLY USED ===
    language: str = Query("en", description="Language code", examples=["en"]),
    country: str = Query("US", description="Country code", examples=["US"]),
    max_results: int = Query(5, ge=1, le=100, description="Max results (1-100)"),
    # === DATE FILTERS ===
    start_date: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="Start date (YYYY-MM-DD)"),
    end_date: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="End date (YYYY-MM-DD)"),
    # === OPTIONS ===
    exclude_duplicates: bool = Query(False, description="Exclude duplicates"),
    # === AUTH ===
    rate_limit_check: None = Depends(rate_limit),
):
    """Get news articles from a specific source."""
    try:
        # Validate input using SourceQuery
        validated_query = SourceQuery(source=source)

        # Normalize the source input by extracting the domain if a URL is provided
        parsed_source = urlparse(validated_query.source)
        domain_source = parsed_source.netloc.lower() if parsed_source.netloc else validated_query.source.lower()
        domain_source = domain_source.replace("www.", "").strip()

        # Parse dates if provided
        start_date_tuple = tuple(map(int, start_date.split("-"))) if start_date else None
        end_date_tuple = tuple(map(int, end_date.split("-"))) if end_date else None

        # Generate cache key
        cache_key = generate_cache_key(
            "gnews:source",
            source=domain_source,
            language=language,
            country=country,
            max_results=max_results,
            start_date=start_date,
            end_date=end_date,
            exclude_duplicates=exclude_duplicates,
        )

        async def fetch_source_news():
            # Create a new GNews instance with start_date and end_date
            gnews = await get_gnews_instance(
                language=language,
                country=country,
                max_results=max_results,
                exclude_duplicates=exclude_duplicates,
                start_date=start_date_tuple,
                end_date=end_date_tuple,
            )

            loop = asyncio.get_event_loop()
            articles = await loop.run_in_executor(None, gnews.get_news, domain_source)
            if not articles:
                raise HTTPException(status_code=404, detail="No articles found for the given parameters.")

            processed_articles = await decode_and_process_articles(articles, filter_by_domain=domain_source)

            return build_news_response(
                processed_articles,
                empty_detail=(f"No articles found from source '{domain_source}' with the given date range."),
            )

        # Get cached result or fetch and cache (10 minute TTL for source news)
        return await get_cached_or_fetch(cache_key, fetch_source_news, ttl=600)

    except ValidationError as ve:
        logger.error(f"Validation error for source '{source}': {ve}")
        raise HTTPException(status_code=400, detail="Invalid source URL or domain.") from ve
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Unexpected error fetching Google News for source '{source}': {e!s}")
        raise HTTPException(status_code=500, detail="Internal Server Error") from e


@gnews_router.get("/search/", summary="Search News", response_model=NewsResponse, response_model_exclude_none=True)
async def search_google_news(
    request: Request,
    # === REQUIRED ===
    query: str = Query(..., description="Search query", examples=["climate change"]),
    # === COMMONLY USED ===
    language: str = Query("en", description="Language code", examples=["en"]),
    country: str = Query("US", description="Country code", examples=["US"]),
    max_results: int = Query(5, ge=1, le=100, description="Max results (1-100)"),
    sort_by: str = Query("relevance", pattern="^(relevance|date)$", description="Sort by: relevance, date"),
    # === DATE FILTERS ===
    start_date: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="Start date (YYYY-MM-DD)"),
    end_date: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="End date (YYYY-MM-DD)"),
    # === OPTIONS ===
    exclude_duplicates: bool = Query(False, description="Exclude duplicates"),
    exact_match: bool = Query(False, description="Exact match only"),
    # === AUTH ===
    rate_limit_check: None = Depends(rate_limit),
):
    """Search news articles by query."""
    try:
        # Parse dates if provided
        start_date_tuple = tuple(map(int, start_date.split("-"))) if start_date else None
        end_date_tuple = tuple(map(int, end_date.split("-"))) if end_date else None

        # Generate cache key
        cache_key = generate_cache_key(
            "gnews:search",
            query=query,
            language=language,
            country=country,
            max_results=max_results,
            start_date=start_date,
            end_date=end_date,
            exclude_duplicates=exclude_duplicates,
            exact_match=exact_match,
            sort_by=sort_by,
        )

        async def fetch_search_results():
            # Create a new GNews instance
            gnews = await get_gnews_instance(
                language=language,
                country=country,
                max_results=max_results,
                exclude_duplicates=exclude_duplicates,
                exact_match=exact_match,
                sort_by=sort_by,
                start_date=start_date_tuple,
                end_date=end_date_tuple,
            )

            loop = asyncio.get_event_loop()
            news = await loop.run_in_executor(None, gnews.get_news, query)

            if not news:
                raise HTTPException(status_code=404, detail="No news found for the given query.")

            processed_articles = await decode_and_process_articles(news)

            return build_news_response(
                processed_articles,
                empty_detail="No processable news found after URL decoding.",
            )

        # Get cached result or fetch and cache
        return await get_cached_or_fetch(cache_key, fetch_search_results)

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error fetching Google News for query '{query}': {e!s}")
        raise HTTPException(status_code=500, detail="Internal Server Error") from e


@gnews_router.get("/top/", summary="Top News", response_model=NewsResponse, response_model_exclude_none=True)
async def get_top_google_news(
    # === COMMONLY USED ===
    language: str = Query("en", description="Language code", examples=["en"]),
    country: str = Query("US", description="Country code", examples=["US"]),
    max_results: int = Query(10, ge=1, le=100, description="Max results (1-100)"),
    # === AUTH ===
    rate_limit_check: None = Depends(rate_limit),
):
    """Get top news articles."""
    try:
        # Generate cache key
        cache_key = generate_cache_key("gnews:top", language=language, country=country, max_results=max_results)

        async def fetch_top_news():
            # Create a new GNews instance
            gnews = await get_gnews_instance(
                language=language,
                country=country,
                max_results=max_results,
            )

            loop = asyncio.get_event_loop()
            top_news = await loop.run_in_executor(None, gnews.get_top_news)

            if not top_news:
                raise HTTPException(status_code=404, detail="No top news found.")

            processed_articles = await decode_and_process_articles(top_news)

            return build_news_response(
                processed_articles,
                empty_detail="No processable top news found after URL decoding.",
            )

        # Get cached result or fetch and cache (use shorter TTL for top news - 5 minutes)
        return await get_cached_or_fetch(cache_key, fetch_top_news, ttl=300)
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error fetching top Google News: {e!s}")
        raise HTTPException(status_code=500, detail="Internal Server Error") from e


@gnews_router.get("/topic/", summary="News by Topic", response_model=NewsResponse, response_model_exclude_none=True)
async def get_news_by_topic(
    # === REQUIRED ===
    topic: str = Query(..., description="Topic name (WORLD, TECHNOLOGY, SPORTS, etc.)", examples=["TECHNOLOGY"]),
    # === COMMONLY USED ===
    language: str = Query("en", description="Language code", examples=["en"]),
    country: str = Query("US", description="Country code", examples=["US"]),
    max_results: int = Query(5, ge=1, le=100, description="Max results (1-100)"),
    # === OPTIONS ===
    exclude_duplicates: bool = Query(False, description="Exclude duplicates"),
    # === AUTH ===
    rate_limit_check: None = Depends(rate_limit),
):
    """Get news articles by topic."""
    if topic.upper() not in AVAILABLE_TOPICS:
        return JSONResponse(
            status_code=400, content={"detail": "Invalid topic provided.", "available_topics": AVAILABLE_TOPICS}
        )
    try:
        # Generate cache key
        cache_key = generate_cache_key(
            "gnews:topic",
            topic=topic.upper(),
            language=language,
            country=country,
            max_results=max_results,
            exclude_duplicates=exclude_duplicates,
        )

        async def fetch_topic_news():
            # Create a new GNews instance without start_date and end_date
            gnews = await get_gnews_instance(
                language=language,
                country=country,
                max_results=max_results,
                exclude_duplicates=exclude_duplicates,
            )

            loop = asyncio.get_event_loop()
            news = await loop.run_in_executor(None, gnews.get_news_by_topic, topic)

            if not news:
                raise HTTPException(status_code=404, detail="No news found for the given topic.")

            processed_articles = await decode_and_process_articles(news)

            return build_news_response(
                processed_articles,
                empty_detail="No processable news found for the topic after URL decoding.",
            )

        # Get cached result or fetch and cache (10 minute TTL for topic news)
        return await get_cached_or_fetch(cache_key, fetch_topic_news, ttl=600)
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error fetching Google News for topic '{topic}': {e!s}")
        raise HTTPException(status_code=500, detail="Internal Server Error") from e


@gnews_router.get(
    "/location/", summary="News by Location", response_model=NewsResponse, response_model_exclude_none=True
)
async def get_news_by_location(
    # === REQUIRED ===
    location: str = Query(..., description="Location name", examples=["New York"]),
    # === COMMONLY USED ===
    language: str = Query("en", description="Language code", examples=["en"]),
    country: str = Query("US", description="Country code", examples=["US"]),
    max_results: int = Query(5, ge=1, le=100, description="Max results (1-100)"),
    # === DATE FILTERS ===
    start_date: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="Start date (YYYY-MM-DD)"),
    end_date: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="End date (YYYY-MM-DD)"),
    # === OPTIONS ===
    exclude_duplicates: bool = Query(False, description="Exclude duplicates"),
    # === AUTH ===
    rate_limit_check: None = Depends(rate_limit),
):
    """Get news articles by location."""
    try:
        # Parse dates if provided
        start_date_tuple = tuple(map(int, start_date.split("-"))) if start_date else None
        end_date_tuple = tuple(map(int, end_date.split("-"))) if end_date else None

        # Generate cache key
        cache_key = generate_cache_key(
            "gnews:location",
            location=location.lower(),
            language=language,
            country=country,
            max_results=max_results,
            start_date=start_date,
            end_date=end_date,
            exclude_duplicates=exclude_duplicates,
        )

        async def fetch_location_news():
            # Create a new GNews instance
            gnews = await get_gnews_instance(
                language=language,
                country=country,
                max_results=max_results,
                exclude_duplicates=exclude_duplicates,
                start_date=start_date_tuple,
                end_date=end_date_tuple,
            )

            loop = asyncio.get_event_loop()
            # URL-encode location to handle spaces and special characters (GNews library bug)
            encoded_location = quote(location)
            news_by_location = await loop.run_in_executor(None, gnews.get_news_by_location, encoded_location)

            if not news_by_location:
                raise HTTPException(status_code=404, detail=f"No news found for the location '{location}'.")

            processed_articles = await decode_and_process_articles(news_by_location)

            return build_news_response(
                processed_articles,
                empty_detail=(f"No processable news found for the location '{location}' after URL decoding."),
            )

        # Get cached result or fetch and cache (10 minute TTL for location news)
        return await get_cached_or_fetch(cache_key, fetch_location_news, ttl=600)
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error fetching Google News for location '{location}': {e!s}")
        raise HTTPException(status_code=500, detail="Internal Server Error") from e


# The `/source/` endpoint definition is already provided above.


@gnews_router.get("/articles/", summary="Bulk Articles", response_model=NewsResponse, response_model_exclude_none=True)
async def get_google_news_articles(
    # === COMMONLY USED ===
    query: str = Query("news", description="Search query", examples=["technology"]),
    language: str = Query("en", description="Language code", examples=["en"]),
    country: str = Query("US", description="Country code", examples=["US"]),
    max_results: int = Query(5, ge=1, le=100, description="Max results (1-100)"),
    # === TIME PERIOD ===
    period: str = Query("1d", pattern=r"^\d+[dwmy]$", description="Period: 7d, 1w, 1m, 1y"),
    # === AUTH ===
    rate_limit_check: None = Depends(rate_limit),
):
    """Get bulk news articles over a time period."""
    try:
        # Generate cache key
        cache_key = generate_cache_key(
            "gnews:articles", query=query, language=language, country=country, max_results=max_results, period=period
        )

        async def fetch_articles():
            # Create a new GNews instance
            gnews = await get_gnews_instance(
                language=language,
                country=country,
                max_results=max_results,
                exclude_duplicates=False,
                period=period,
            )

            loop = asyncio.get_event_loop()
            articles = await loop.run_in_executor(None, gnews.get_news, query)
            if not articles:
                raise HTTPException(status_code=404, detail="No articles found for the given parameters.")

            processed_articles = await decode_and_process_articles(articles)

            return build_news_response(
                processed_articles,
                empty_detail=("No processable articles found for the given parameters after URL decoding."),
            )

        # Get cached result or fetch and cache (10 minute TTL for bulk articles)
        return await get_cached_or_fetch(cache_key, fetch_articles, ttl=600)

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error fetching Google News articles for query '{query}': {e!s}")
        raise HTTPException(status_code=500, detail="Internal Server Error") from e


@gnews_router.get("/article-details/", summary="Article Details", response_model=dict)
async def get_article_details(
    # === REQUIRED ===
    url: str = Query(..., description="Article URL to analyze"),
    # === AUTH ===
    rate_limit_check: None = Depends(rate_limit),
):
    """Get detailed article information (title, text, summary, keywords)."""
    try:
        # Validate BEFORE anything else touches the URL: no fetch, no cache
        # lookup, no logging of the target at info level.
        validated = validate_article_url(url)
    except UrlNotAllowed as exc:
        # Detail to the logs, generic message to the caller.
        logger.warning("Blocked outbound article fetch: %s", exc.reason)
        raise HTTPException(status_code=400, detail=exc.public_message) from exc

    target_url = validated.url
    logger.info("Fetching article details for allow-listed host %s", validated.host)

    try:
        # Key on the validated URL, so two spellings of the same target share
        # an entry and an unvalidated string can never seed the cache.
        url_hash = hashlib.sha256(target_url.encode()).hexdigest()
        cache_key = generate_cache_key("gnews:article_details", url_hash=url_hash)

        async def fetch_article_details():
            # Ensure NLTK is set up (only runs once)
            await ensure_nltk_setup()

            # Fetch here rather than letting newspaper do it. Newspaper follows
            # redirects itself, and a redirect is a second, unvalidated request
            # -- an allow-listed host answering "302 -> http://169.254.169.254"
            # would walk straight past the check above. fetch_allow_listed_html
            # re-validates every hop.
            html, final_url = await fetch_allow_listed_html(target_url)

            config = Config()
            config.request_timeout = settings.HTTP_READ_TIMEOUT  # Use configured timeout
            config.thread_timeout = settings.HTTP_READ_TIMEOUT

            loop = asyncio.get_event_loop()

            # input_html means newspaper parses what we already fetched and
            # makes no network request of its own.
            article = Article(final_url, config=config)
            await loop.run_in_executor(None, article.download, html)

            # Parse article
            await loop.run_in_executor(None, article.parse)

            # Try NLP processing
            nlp_success = True
            try:
                await loop.run_in_executor(None, article.nlp)
            except (LookupError, ImportError) as le:
                # Two different states, and they must not be cached the same way.
                #
                # ImportError: nltk is not installed. That is the steady state
                # while PYSEC-2026-3740 is unfixed, not a transient gap, so the response is
                # as complete as it will ever be and is safe to cache.
                #
                # LookupError: nltk IS installed but the punkt corpus is missing.
                # That is transient - it resolves the moment the corpus lands -
                # so the response must not be pinned for the TTL. is_cacheable()
                # refuses anything carrying an `error` key, which is how.
                nlp_permanently_absent = isinstance(le, ImportError)
                logger.warning("NLP unavailable for %s: %s", validated.host, le)
                nlp_success = False

            # Build response (convert publish_date to string for JSON serialization)
            publish_date_str = None
            if article.publish_date:
                publish_date_str = (
                    article.publish_date.isoformat()
                    if hasattr(article.publish_date, "isoformat")
                    else str(article.publish_date)
                )

            response_data = {
                "title": article.title,
                "authors": article.authors,
                "publish_date": publish_date_str,
                "text": article.text,
                "top_image": article.top_image,
                "images": list(article.images),
                "videos": article.movies,
                "meta_data": article.meta_data,
                "meta_description": article.meta_description,
                "meta_keywords": article.meta_keywords,
            }

            if nlp_success:
                response_data.update({"summary": article.summary, "keywords": article.keywords})
            else:
                response_data["summary"] = None
                response_data["keywords"] = None
                response_data["nlp_available"] = False
                if not nlp_permanently_absent:
                    # Transient: keep the key that stops is_cacheable() storing it.
                    response_data["error"] = "Unable to perform NLP analysis due to missing NLTK resource."

            return response_data

        # Get cached result or fetch and cache (1 hour TTL - article content doesn't change)
        return await get_cached_or_fetch(cache_key, fetch_article_details, ttl=3600)

    except HTTPException:
        raise
    except UrlNotAllowed as exc:
        # A redirect hop failed validation. Reported exactly like any other
        # fetch failure, so the response cannot say whether the allow-listed
        # host tried to redirect us somewhere internal.
        logger.warning("Blocked redirect while fetching article: %s", exc.reason)
        raise HTTPException(status_code=502, detail=ARTICLE_FETCH_FAILED_DETAIL) from exc
    except ArticleException as ae:
        # The old body echoed the target host and the underlying failure, which
        # let a caller tell "port closed" from "port open but not HTML" and use
        # the endpoint as an internal port scanner. Cause goes to the logs only.
        logger.error("Newspaper error fetching %s: %s", validated.host, ae)
        raise HTTPException(status_code=502, detail=ARTICLE_FETCH_FAILED_DETAIL) from ae
    except Exception as e:
        logger.error(
            "Unexpected error fetching article details for %s: %s",
            validated.host,
            e,
            exc_info=True,
        )
        raise HTTPException(status_code=502, detail=ARTICLE_FETCH_FAILED_DETAIL) from e
