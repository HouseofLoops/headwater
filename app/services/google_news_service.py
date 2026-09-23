"""
Google News service.

Business logic behind the /google-news endpoints: building GNews clients,
decoding Google News redirect URLs, normalising articles, turning processed
articles into responses, and the namespaced response cache.

Moved verbatim out of app.api.google_news.google_news_api, which keeps the
route handlers and still re-exports every name defined here.
"""
from fastapi import HTTPException
from gnews import GNews
from typing import List, Optional
import logging
import json
import asyncio
from urllib.parse import quote, urlparse
import httpx
from selectolax.parser import HTMLParser
from app.core.proxy import get_proxy, mask_proxy
import datetime
from app.core.cache_manager import cache_manager
from app.core.config import get_settings
from app.core.http_client import get_http_client_manager
from app.core.constants import USER_AGENTS
import hashlib
from typing import Any, Callable, Iterable
from app.core.log_safety import scrub

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# GNews resolves every Google News redirect by launching a *whole Chromium
# browser per article* (gnews/utils/utils.py:resolve_url -> _resolve_with_
# playwright), with no reuse and a 10s wait_for_url timeout. Measured at
# ~16s per article: a five-article search spent 80.1s, of which only 2.5s was
# network. Behind a proxy the redirect often misses that 10s window, so the
# full timeout is burned every time.
#
# It is also redundant. decode_and_process_articles() below decodes the same
# URLs through Google's own parameters in ~0.2s for five articles, which is
# what the response actually uses. Turning the browser pass off took the same
# query from 80.8s to 2.9s with byte-identical article URLs.
#
# Restore by deleting this block if GNews ever changes how process_url works.
# ---------------------------------------------------------------------------
import gnews.utils.utils as _gnews_utils


def _skip_gnews_url_resolution(url: str, proxies: Optional[dict] = None) -> str:
    """Leave the URL alone; decode_google_news_url() resolves it far faster."""
    return url


_gnews_utils.resolve_url = _skip_gnews_url_resolution


# Global cache manager instance
settings = get_settings()


# -----------------------------------------------------------------------------
# Cache helpers
#
# Google News keys live in their own cache namespace so a key here can never
# collide with one written by another router.
# -----------------------------------------------------------------------------
CACHE_NAMESPACE = "gnews"


def generate_cache_key(base_key: str, **params) -> str:
    """Build a deterministic, namespaced cache key.

    Parameters are sorted so that the same request always produces the same
    key regardless of keyword order, and ``None`` values are dropped so that
    an unset optional parameter and an absent one share a key.
    """
    parts = [CACHE_NAMESPACE, base_key]
    for name, value in sorted(params.items()):
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            value = ",".join(str(item) for item in value)
        parts.append(f"{name}:{value}")

    key = ":".join(parts)
    # Redis keys are capped; hash the tail rather than truncating, so two long
    # keys that share a prefix cannot collide.
    if len(key) > 250:
        digest = hashlib.sha256(key.encode()).hexdigest()
        key = f"{CACHE_NAMESPACE}:{base_key}:{digest}"
    return key


def is_cacheable(value: Any) -> bool:
    """Return False for degraded results that must not outlive the request.

    Two shapes are refused, both for the same reason: they are successes that
    are missing something, and storing one pins that gap for the whole TTL.

    * ``partial`` -- some articles were lost this time. Cache it and the short
      list is served to everyone until it expires.
    * ``error`` -- the article was fetched but part of the processing failed
      *transiently*: nltk is installed and the punkt corpus is not. Caching it
      means that even once the corpus lands, callers keep getting the
      summary-less version for an hour.

      Note the case this deliberately does NOT refuse: nltk not being installed
      at all. That is a steady state, not a gap, so those responses carry
      ``nlp_available: false`` and no ``error`` key, and are cached normally.
      Refusing them would disable caching for this endpoint entirely.
    """
    if not isinstance(value, dict):
        return True
    return not (value.get("partial") or value.get("error"))


async def get_cached_or_fetch(
    cache_key: str,
    fetch_func: Callable[[], Any],
    ttl: Optional[int] = None,
    should_cache: Callable[[Any], bool] = is_cacheable,
) -> Any:
    """Return the cached value for ``cache_key``, or fetch and store it.

    ``fetch_func`` exceptions -- including the ``HTTPException`` raised when
    Google News gives us nothing usable -- propagate untouched and nothing is
    written to the cache. Caching a failure as if it were a result is how one
    upstream blip becomes an hour of wrong answers.

    Args:
        should_cache: Decides whether a successful result is worth keeping.
            Defaults to :func:`is_cacheable`, which refuses partial results.
    """
    if not getattr(settings, "ENABLE_CACHE", True):
        return await fetch_func()

    cached = await cache_manager.get(cache_key, namespace=CACHE_NAMESPACE)
    if cached is not None:
        logger.debug("Cache hit for %s", scrub(cache_key))
        return cached

    data = await fetch_func()
    if should_cache(data):
        await cache_manager.set(cache_key, data, ttl=ttl, namespace=CACHE_NAMESPACE)
    else:
        logger.info("Not caching a partial result for %s", scrub(cache_key))
    return data


# Use centralized HTTP client manager for GNews operations
async def get_gnews_http_client(proxy_url: Optional[str] = None) -> httpx.AsyncClient:
    """
    Get a shared HTTP client for GNews operations using centralized HTTPClientManager.

    Args:
        proxy_url: Optional proxy URL

    Returns:
        httpx.AsyncClient: Shared HTTP client from connection pool
    """
    http_manager = get_http_client_manager()
    return await http_manager.get_client(proxy_url)


# -----------------------------------------------------------------------------
# Decoding functions
# -----------------------------------------------------------------------------
GOOGLE_NEWS_HOSTS = ("news.google.com",)


def is_google_news_redirect(url: str) -> bool:
    """Return True when ``url`` still points at Google News and needs decoding.

    gnews >= 0.5 resolves article links itself when the optional Playwright
    extra is installed, so ``article["url"]`` is usually already the
    publisher's URL. When resolution is unavailable or fails, gnews falls back
    to the original ``news.google.com/rss/articles/...`` link. Both forms
    therefore reach us, and only the second one needs the decode round-trip.
    """
    if not url:
        return False
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return any(
        host == entry or host.endswith(f".{entry}") for entry in GOOGLE_NEWS_HOSTS
    )


async def get_base64_str(source_url):
    """
    Extracts the base64 string from a Google News URL.
    """
    try:
        url = urlparse(source_url)
        path = url.path.split("/")
        if (
            is_google_news_redirect(source_url)
            and len(path) > 1
            and path[-2] in ["articles", "read", "rss"]
        ):
            return {"status": True, "base64_str": path[-1]}
        return {"status": False, "message": "Invalid Google News URL format."}
    except Exception as e:
        return {"status": False, "message": f"Error in get_base64_str: {str(e)}"}

async def get_decoding_params(base64_str):
    """
    Fetches signature and timestamp required for decoding from Google News.
    """
    try:
        url = f"https://news.google.com/rss/articles/{base64_str}"
        proxy_url = await get_proxy()  # Adjust based on your implementation

        client = await get_gnews_http_client(proxy_url=proxy_url)
        response = await client.get(url)
        response.raise_for_status()

        parser = HTMLParser(response.text)
        data_element = parser.css_first("c-wiz > div[jscontroller]")
        if data_element is None:
            return {
                "status": False,
                "message": "Failed to fetch data attributes from Google News with the RSS URL.",
            }

        return {
            "status": True,
            "signature": data_element.attributes.get("data-n-a-sg"),
            "timestamp": data_element.attributes.get("data-n-a-ts"),
            "base64_str": base64_str,
        }

    except httpx.RequestError as rss_req_err:
        return {
            "status": False,
            "message": f"Request error in get_decoding_params with RSS URL: {str(rss_req_err)}",
        }
    except Exception as e:
        return {
            "status": False,
            "message": f"Unexpected error in get_decoding_params: {str(e)}",
        }

def validate_date_format(date_str):
    try:
        datetime.datetime.strptime(date_str, '%Y-%m-%d')
        return True
    except ValueError:
        return False

async def decode_url(signature, timestamp, base64_str, start_date=None, end_date=None):
    """
    Decodes the Google News URL using the signature and timestamp.
    """
    try:
        # Validate date formats
        if start_date and not validate_date_format(start_date):
            logger.error(f"Invalid start_date format: {start_date}. Expected format: YYYY-MM-DD")
            return {
                "status": False,
                "message": f"Invalid start_date format: {start_date}. Expected format: YYYY-MM-DD",
            }
        if end_date and not validate_date_format(end_date):
            logger.error(f"Invalid end_date format: {end_date}. Expected format: YYYY-MM-DD")
            return {
                "status": False,
                "message": f"Invalid end_date format: {end_date}. Expected format: YYYY-MM-DD",
            }

        url = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
        payload = [
            "Fbv4je",
            f'["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],"X","X",1,[1,1,1],1,1,null,0],"{base64_str}",{timestamp},"{signature}"]',
        ]
        headers = {
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "User-Agent": USER_AGENTS["windows_chrome"],
        }

        proxy_url = await get_proxy()  # Adjust based on your implementation

        client = await get_gnews_http_client(proxy_url=proxy_url)
        response = await client.post(
            url,
            headers=headers,
            data=f"f.req={quote(json.dumps([[payload]]))}"
        )
        response.raise_for_status()

        parsed_data = json.loads(response.text.split("\n\n")[1])[:-2]
        decoded_url = json.loads(parsed_data[0][2])[1]
        return {"status": True, "decoded_url": decoded_url}
    except httpx.RequestError as req_err:
        logger.error(f"Request error in decode_url: {str(req_err)}")
        return {
            "status": False,
            "message": f"Request error in decode_url: {str(req_err)}",
        }
    except (json.JSONDecodeError, IndexError, TypeError) as parse_err:
        logger.error(f"Parsing error in decode_url: {str(parse_err)}")
        return {
            "status": False,
            "message": f"Parsing error in decode_url: {str(parse_err)}",
        }
    except Exception as e:
        logger.error(f"Error in decode_url: {str(e)}")
        return {"status": False, "message": f"Error in decode_url: {str(e)}"}

async def decode_google_news_url(source_url, interval=None):
    """
    Decodes a Google News article URL into its original source URL.
    """
    try:
        base64_response = await get_base64_str(source_url)
        if not base64_response["status"]:
            return base64_response

        decoding_params_response = await get_decoding_params(base64_response["base64_str"])
        if not decoding_params_response["status"]:
            return decoding_params_response

        decoded_url_response = await decode_url(
            decoding_params_response["signature"],
            decoding_params_response["timestamp"],
            decoding_params_response["base64_str"],
        )
        if interval:
            await asyncio.sleep(interval)

        return decoded_url_response
    except Exception as e:
        return {
            "status": False,
            "message": f"Error in decode_google_news_url: {str(e)}",
        }


# -----------------------------------------------------------------------------
# Helper function to create a new GNews instance per request
# -----------------------------------------------------------------------------
async def get_gnews_instance(
    language: str,
    country: str,
    max_results: int,
    exclude_duplicates: bool = False,
    exact_match: bool = False,
    sort_by: str = "relevance",
    period: Optional[str] = None,
    start_date: Optional[tuple] = None,
    end_date: Optional[tuple] = None,
) -> GNews:
    proxy_url_val = await get_proxy()

    # GNews hands this straight to requests as `proxies=`, which only accepts a
    # mapping -- its own signature is `proxy: dict | None`. get_proxy() returns a
    # single URL string, so passing it through raised "proxies must be a mapping"
    # and every search 500'd the moment ENABLE_PROXY was turned on. The httpx
    # paths above are unaffected: httpx does take a bare URL.
    proxy_map = (
        {"http": proxy_url_val, "https": proxy_url_val} if proxy_url_val else None
    )

    # Initialize GNews with proxy for its internal feedparser usage
    gnews = GNews(
        language=language,
        country=country,
        max_results=max_results,
        period=period,
        start_date=start_date,
        end_date=end_date,
        # exclude_websites can be set if needed, GNews constructor supports it
        proxy=proxy_map  # requests-style {scheme: url} mapping, not a bare URL
    )

    # Set attributes not available in constructor or that need to be dynamically set
    gnews.exclude_duplicates = exclude_duplicates
    gnews.exact_match = exact_match
    gnews.sort_by = sort_by
    # Period, start_date, end_date are already set via constructor if provided

    # Set up httpx.AsyncClient on gnews.session for any parts of GNews that might use it
    # (or for future use/consistency, as the original code did this).
    if proxy_url_val:
        mounts = {
            "http://": httpx.AsyncHTTPTransport(proxy=proxy_url_val),
            "https://": httpx.AsyncHTTPTransport(proxy=proxy_url_val),
        }
        gnews.session = httpx.AsyncClient(mounts=mounts)
        logger.debug("GNews instance using proxy for httpx session: %s", mask_proxy(proxy_url_val))
        if proxy_url_val: # Logging for clarity that proxy is also set for feedparser
            logger.debug(f"GNews instance also configured with proxy for feedparser: {proxy_url_val}")
    else:
        gnews.session = httpx.AsyncClient()
        logger.debug("GNews instance not using any proxy for httpx session or feedparser.")

    return gnews


# -----------------------------------------------------------------------------
# Helper: Decode and Process Articles (Concurrent Version)
# -----------------------------------------------------------------------------
class ProcessedArticles(list):
    """Processed articles plus a count of the ones that could not be produced.

    A plain list cannot distinguish "Google News had nothing to say" from
    "every article was thrown away because we failed to resolve it", and the
    old code returned the same empty list for both. Callers use ``failed`` to
    answer honestly: a 502 when the pipeline broke, ``partial`` when only some
    articles survived.

    Subclassing ``list`` keeps the value usable anywhere a list was expected,
    including in tests that patch this function with a plain list -- callers
    read the counters with ``getattr(..., "failed", 0)``.
    """

    def __init__(
        self,
        articles: Iterable[dict] = (),
        *,
        total: int = 0,
        failed: int = 0,
        filtered_out: int = 0,
    ) -> None:
        super().__init__(articles)
        self.total = total
        self.failed = failed
        self.filtered_out = filtered_out

    @property
    def partial(self) -> bool:
        """True when at least one article was lost to a failure."""
        return self.failed > 0


async def decode_and_process_articles(
    raw_articles: List[dict],
    filter_by_domain: Optional[str] = None,
    max_concurrent: int = 10
) -> ProcessedArticles:
    """
    Normalise a batch of gnews articles, decoding Google News redirects.

    gnews returns either a resolved publisher URL or -- when its own
    resolution is unavailable -- the original ``news.google.com`` redirect.
    Articles of the first kind are used as they are; only the second kind
    goes through :func:`decode_google_news_url`. Requiring the redirect form
    is what made every endpoint answer 404: with gnews >= 0.5 no article
    matched, so the list always emptied.

    Args:
        raw_articles: List of raw article dictionaries
        filter_by_domain: Optional domain to filter by
        max_concurrent: Maximum number of concurrent decoding operations

    Returns:
        A :class:`ProcessedArticles` list. ``failed`` counts articles lost to
        an error (as opposed to ones deliberately filtered out by
        ``filter_by_domain``), so callers can report a partial result or an
        upstream failure instead of silently returning fewer articles.
    """
    if not raw_articles:
        return ProcessedArticles()

    # Create semaphore to limit concurrent operations
    semaphore = asyncio.Semaphore(max_concurrent)

    # Outcome markers: distinguishing "dropped on purpose" from "dropped
    # because something broke" is the whole point of this pass.
    FILTERED = "filtered"
    FAILED = "failed"

    async def decode_single_article(article_data: dict):
        """Resolve and transform a single article with semaphore control."""
        async with semaphore:
            source_url = article_data.get("url")
            if not source_url:
                logger.warning(
                    "Article %r has no URL; dropping it",
                    article_data.get("title", "N/A"),
                )
                return FAILED

            if is_google_news_redirect(source_url):
                decoded_result = await decode_google_news_url(source_url)
                if not decoded_result.get("status"):
                    logger.warning(
                        "Could not decode Google News URL for article %r: %s",
                        article_data.get("title", "N/A"),
                        decoded_result.get("message"),
                    )
                    return FAILED
                article_data["url"] = decoded_result["decoded_url"]
            else:
                # gnews already resolved this one to the publisher's URL.
                logger.debug("Article URL already resolved: %s", source_url)

            transformed_article = transform_article(article_data)

            if filter_by_domain:
                article_domain = (
                    urlparse(transformed_article["url"])
                    .netloc.lower()
                    .replace("www.", "")
                    .strip()
                )
                if filter_by_domain not in article_domain:
                    logger.debug(
                        "Skipping article %r: domain %r does not match %r",
                        transformed_article["title"], article_domain, filter_by_domain,
                    )
                    return FILTERED

            return transformed_article

    tasks = [decode_single_article(article) for article in raw_articles]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    processed_articles: List[dict] = []
    failed = 0
    filtered_out = 0
    for result in results:
        if isinstance(result, BaseException):
            # An unexpected error is still a lost article, never a silent skip.
            logger.error("Unexpected error processing article: %s", result, exc_info=result)
            failed += 1
        elif result is FAILED:
            failed += 1
        elif result is FILTERED:
            filtered_out += 1
        else:
            processed_articles.append(result)

    logger.info(
        "Processed %d of %d articles (%d failed, %d filtered out)",
        len(processed_articles), len(raw_articles), failed, filtered_out,
    )
    return ProcessedArticles(
        processed_articles,
        total=len(raw_articles),
        failed=failed,
        filtered_out=filtered_out,
    )


# Identical for every cause: what failed upstream is a log detail, not
# something to describe to an unauthenticated caller.
UPSTREAM_NEWS_FAILURE_DETAIL = (
    "Could not resolve articles from Google News. Please retry."
)


def build_news_response(processed_articles, *, empty_detail: str) -> dict:
    """Turn processed articles into a response, or raise the honest error.

    Three outcomes, previously collapsed into "404, no articles":

    * nothing survived and everything failed -> 502, the pipeline is broken
    * nothing survived and nothing failed    -> 404, there genuinely is nothing
    * some survived, some failed             -> 200 with ``partial: true``

    ``getattr`` is used for the counters so that a plain list still works.
    """
    failed = getattr(processed_articles, "failed", 0)

    if not processed_articles:
        if failed:
            logger.error(
                "Dropped all %d articles while resolving Google News URLs",
                failed,
            )
            raise HTTPException(status_code=502, detail=UPSTREAM_NEWS_FAILURE_DETAIL)
        raise HTTPException(status_code=404, detail=empty_detail)

    response: dict = {"articles": list(processed_articles)}
    if failed:
        # Never hand back a silently shortened list.
        response["partial"] = True
        response["dropped"] = failed
    return response


# -----------------------------------------------------------------------------
# Helper: Transform Article Data
# -----------------------------------------------------------------------------
def transform_article(article: dict) -> dict:
    return {
        "title": article.get("title"),
        "description": article.get("description"),
        "published_date": article.get("published date"),
        "url": article.get("url"),
        "publisher": article.get("publisher", {}).get("title") if article.get("publisher") else None
    }
