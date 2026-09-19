from fastapi import APIRouter, HTTPException, Query, Depends, Request  # Ensure Depends is imported if not already
from fastapi.responses import JSONResponse
from gnews import GNews
from newspaper import Article, Config, ArticleException
from typing import List, Optional  # Ensure Optional is imported
import logging
import json
import asyncio
from urllib.parse import quote, urlparse
import httpx
from selectolax.parser import HTMLParser
import os
import nltk
from pydantic import BaseModel, validator, ValidationError
import re
from app.core.proxy import get_proxy  # adjust if needed, mask_proxy

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
import datetime
from app.core.rate_limiter import rate_limit
from app.core.cache_manager import cache_manager
from app.core.config import get_settings
from app.core.http_client import get_http_client_manager
from app.core.constants import USER_AGENTS
from app.core.url_guard import NEWS_ALLOWED_HOSTS, UrlNotAllowed, validate_outbound_url
import hashlib
from typing import Any, Callable, Iterable, Tuple
from app.core.log_safety import scrub

# Initialize NLTK asynchronously at module level
async def setup_nltk():
    """Setup NLTK resources once at startup."""
    try:
        # Set NLTK data path to a writable directory
        nltk_data_dir = os.path.join(os.getcwd(), "nltk_data")
        os.makedirs(nltk_data_dir, exist_ok=True)
        nltk.data.path.insert(0, nltk_data_dir)
        
        # Check if 'punkt_tab' is already downloaded
        try:
            nltk.data.find('tokenizers/punkt_tab')
            logger.info("NLTK 'punkt_tab' resource already available.")
        except LookupError:
            # 'punkt_tab' not found, so download it
            logger.info("NLTK 'punkt_tab' resource not found. Downloading...")
            nltk.download('punkt_tab', nltk_data_dir, quiet=True)
            logger.info("NLTK 'punkt_tab' resource downloaded successfully.")
            
        # Also download 'punkt' as fallback
        try:
            nltk.data.find('tokenizers/punkt')
        except LookupError:
            logger.info("Downloading fallback 'punkt' resource...")
            nltk.download('punkt', nltk_data_dir, quiet=True)
            
    except Exception as e:
        # Handle any other exceptions during NLTK setup
        logger.error(f"An error occurred during NLTK setup: {e}")

# Run NLTK setup at import time (this will be awaited in the lifespan event)
_nltk_setup_task = None

async def ensure_nltk_setup():
    """Ensure NLTK is set up, running setup only once."""
    global _nltk_setup_task
    if _nltk_setup_task is None:
        _nltk_setup_task = asyncio.create_task(setup_nltk())
    await _nltk_setup_task

# Initialize Google News API Router
gnews_router = APIRouter()
logger = logging.getLogger(__name__)
# logging.basicConfig(level=logging.DEBUG)  # Ensure DEBUG level logs are captured -> This should be handled by the main application entry point

# Pydantic Model for Input Validation
class SourceQuery(BaseModel):
    source: str

    @validator('source')
    def validate_source(cls, v):
        # Optimized regex to validate domain names or full URLs
        pattern = r'^(https?://)?(www\.)?([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}$'
        if not re.fullmatch(pattern, v):
            raise ValueError('Invalid source URL or domain.')
        return v

AVAILABLE_TOPICS = [
    "WORLD", "NATION", "BUSINESS", "TECHNOLOGY", "ENTERTAINMENT", "SPORTS", "SCIENCE", "HEALTH",
    "POLITICS", "CELEBRITIES", "TV", "MUSIC", "MOVIES", "THEATER", "SOCCER", "CYCLING",
    "MOTOR SPORTS", "TENNIS", "COMBAT SPORTS", "BASKETBALL", "BASEBALL", "FOOTBALL",
    "SPORTS BETTING", "WATER SPORTS", "HOCKEY", "GOLF", "CRICKET", "RUGBY", "ECONOMY",
    "PERSONAL FINANCE", "FINANCE", "DIGITAL CURRENCIES", "MOBILE", "ENERGY", "GAMING",
    "INTERNET SECURITY", "GADGETS", "VIRTUAL REALITY", "ROBOTICS", "NUTRITION", "PUBLIC HEALTH",
    "MENTAL HEALTH", "MEDICINE", "SPACE", "WILDLIFE", "ENVIRONMENT", "NEUROSCIENCE",
    "PHYSICS", "GEOLOGY", "PALEONTOLOGY", "SOCIAL SCIENCES", "EDUCATION", "JOBS",
    "ONLINE EDUCATION", "HIGHER EDUCATION", "VEHICLES", "ARTS-DESIGN", "BEAUTY", "FOOD",
    "TRAVEL", "SHOPPING", "HOME", "OUTDOORS", "FASHION"
]

AVAILABLE_LANGUAGES = {
    'english': 'en',
    'indonesian': 'id',
    'czech': 'cs',
    'german': 'de',
    'spanish': 'es-419',
    'french': 'fr',
    'italian': 'it',
    'latvian': 'lv',
    'lithuanian': 'lt',
    'hungarian': 'hu',
    'dutch': 'nl',
    'norwegian': 'no',
    'polish': 'pl',
    'portuguese brasil': 'pt-419',
    'portuguese portugal': 'pt-150',
    'romanian': 'ro',
    'slovak': 'sk',
    'slovenian': 'sl',
    'swedish': 'sv',
    'vietnamese': 'vi',
    'turkish': 'tr',
    'greek': 'el',
    'bulgarian': 'bg',
    'russian': 'ru',
    'serbian': 'sr',
    'ukrainian': 'uk',
    'hebrew': 'he',
    'arabic': 'ar',
    'marathi': 'mr',
    'hindi': 'hi',
    'bengali': 'bn',
    'tamil': 'ta',
    'telugu': 'te',
    'malyalam': 'ml',
    'thai': 'th',
    'chinese simplified': 'zh-Hans',
    'chinese traditional': 'zh-Hant',
    'japanese': 'ja',
    'korean': 'ko'
}

AVAILABLE_COUNTRIES = {
    'Australia': 'AU',
    'Botswana': 'BW',
    'Canada': 'CA',
    'Ethiopia': 'ET',
    'Ghana': 'GH',
    'India': 'IN',
    'Indonesia': 'ID',
    'Ireland': 'IE',
    'Israel': 'IL',
    'Kenya': 'KE',
    'Latvia': 'LV',
    'Malaysia': 'MY',
    'Namibia': 'NA',
    'New Zealand': 'NZ',
    'Nigeria': 'NG',
    'Pakistan': 'PK',
    'Philippines': 'PH',
    'Singapore': 'SG',
    'South Africa': 'ZA',
    'Tanzania': 'TZ',
    'Uganda': 'UG',
    'United Kingdom': 'GB',
    'United States': 'US',
    'Zimbabwe': 'ZW',
    'Czech Republic': 'CZ',
    'Germany': 'DE',
    'Austria': 'AT',
    'Switzerland': 'CH',
    'Argentina': 'AR',
    'Chile': 'CL',
    'Colombia': 'CO',
    'Cuba': 'CU',
    'Mexico': 'MX',
    'Peru': 'PE',
    'Venezuela': 'VE',
    'Belgium': 'BE',
    'France': 'FR',
    'Morocco': 'MA',
    'Senegal': 'SN',
    'Italy': 'IT',
    'Lithuania': 'LT',
    'Hungary': 'HU',
    'Netherlands': 'NL',
    'Norway': 'NO',
    'Poland': 'PL',
    'Brazil': 'BR',
    'Portugal': 'PT',
    'Romania': 'RO',
    'Slovakia': 'SK',
    'Slovenia': 'SI',
    'Sweden': 'SE',
    'Vietnam': 'VN',
    'Turkey': 'TR',
    'Greece': 'GR',
    'Bulgaria': 'BG',
    'Russia': 'RU',
    'Ukraine': 'UA',
    'Serbia': 'RS',
    'United Arab Emirates': 'AE',
    'Saudi Arabia': 'SA',
    'Lebanon': 'LB',
    'Egypt': 'EG',
    'Bangladesh': 'BD',
    'Thailand': 'TH',
    'China': 'CN',
    'Taiwan': 'TW',
    'Hong Kong': 'HK',
    'Japan': 'JP',
    'Republic of Korea': 'KR'
}

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
      (NLP, when the NLTK corpus is unavailable). Caching it means that even
      once the corpus is installed, callers keep getting the summary-less
      version for an hour.
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

# -----------------------------------------------------------------------------
# Pydantic Models for Responses
# -----------------------------------------------------------------------------
class NewsArticle(BaseModel):
    title: str
    published_date: str
    description: Optional[str]
    url: str
    publisher: Optional[str]

class NewsResponse(BaseModel):
    """A list of articles, plus a signal when some were lost.

    ``partial`` and ``dropped`` are omitted (via
    ``response_model_exclude_none``) on a clean response, so a complete result
    is exactly ``{"articles": [...]}`` as before. When articles were dropped,
    the caller is told rather than being handed a short list that looks whole.
    """

    articles: List[NewsArticle]
    partial: Optional[bool] = None
    dropped: Optional[int] = None

class ErrorResponse(BaseModel):
    detail: str


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
    source: str = Query(..., description="Source domain or URL", example="cnn.com"),
    # === COMMONLY USED ===
    language: str = Query("en", description="Language code", example="en"),
    country: str = Query("US", description="Country code", example="US"),
    max_results: int = Query(5, ge=1, le=100, description="Max results (1-100)"),
    # === DATE FILTERS ===
    start_date: Optional[str] = Query(None, regex=r"^\d{4}-\d{2}-\d{2}$", description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, regex=r"^\d{4}-\d{2}-\d{2}$", description="End date (YYYY-MM-DD)"),
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
        domain_source = domain_source.replace('www.', '').strip()

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
            exclude_duplicates=exclude_duplicates
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
                empty_detail=(
                    f"No articles found from source '{domain_source}' "
                    "with the given date range."
                ),
            )

        # Get cached result or fetch and cache (10 minute TTL for source news)
        return await get_cached_or_fetch(cache_key, fetch_source_news, ttl=600)

    except ValidationError as ve:
        logger.error(f"Validation error for source '{source}': {ve}")
        raise HTTPException(status_code=400, detail="Invalid source URL or domain.")
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Unexpected error fetching Google News for source '{source}': {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

@gnews_router.get("/search/", summary="Search News", response_model=NewsResponse, response_model_exclude_none=True)
async def search_google_news(
    request: Request,
    # === REQUIRED ===
    query: str = Query(..., description="Search query", example="climate change"),
    # === COMMONLY USED ===
    language: str = Query("en", description="Language code", example="en"),
    country: str = Query("US", description="Country code", example="US"),
    max_results: int = Query(5, ge=1, le=100, description="Max results (1-100)"),
    sort_by: str = Query("relevance", regex="^(relevance|date)$", description="Sort by: relevance, date"),
    # === DATE FILTERS ===
    start_date: Optional[str] = Query(None, regex=r"^\d{4}-\d{2}-\d{2}$", description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, regex=r"^\d{4}-\d{2}-\d{2}$", description="End date (YYYY-MM-DD)"),
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
            sort_by=sort_by
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
        logger.error(f"Error fetching Google News for query '{query}': {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

@gnews_router.get("/top/", summary="Top News", response_model=NewsResponse, response_model_exclude_none=True)
async def get_top_google_news(
    # === COMMONLY USED ===
    language: str = Query("en", description="Language code", example="en"),
    country: str = Query("US", description="Country code", example="US"),
    max_results: int = Query(10, ge=1, le=100, description="Max results (1-100)"),
    # === AUTH ===
    rate_limit_check: None = Depends(rate_limit),
):
    """Get top news articles."""
    try:
        # Generate cache key
        cache_key = generate_cache_key(
            "gnews:top",
            language=language,
            country=country,
            max_results=max_results
        )

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
        logger.error(f"Error fetching top Google News: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

@gnews_router.get("/topic/", summary="News by Topic", response_model=NewsResponse, response_model_exclude_none=True)
async def get_news_by_topic(
    # === REQUIRED ===
    topic: str = Query(..., description="Topic name (WORLD, TECHNOLOGY, SPORTS, etc.)", example="TECHNOLOGY"),
    # === COMMONLY USED ===
    language: str = Query("en", description="Language code", example="en"),
    country: str = Query("US", description="Country code", example="US"),
    max_results: int = Query(5, ge=1, le=100, description="Max results (1-100)"),
    # === OPTIONS ===
    exclude_duplicates: bool = Query(False, description="Exclude duplicates"),
    # === AUTH ===
    rate_limit_check: None = Depends(rate_limit),
):
    """Get news articles by topic."""
    if topic.upper() not in AVAILABLE_TOPICS:
        return JSONResponse(
            status_code=400,
            content={"detail": "Invalid topic provided.", "available_topics": AVAILABLE_TOPICS}
        )
    try:
        # Generate cache key
        cache_key = generate_cache_key(
            "gnews:topic",
            topic=topic.upper(),
            language=language,
            country=country,
            max_results=max_results,
            exclude_duplicates=exclude_duplicates
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
        logger.error(f"Error fetching Google News for topic '{topic}': {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

@gnews_router.get("/location/", summary="News by Location", response_model=NewsResponse, response_model_exclude_none=True)
async def get_news_by_location(
    # === REQUIRED ===
    location: str = Query(..., description="Location name", example="New York"),
    # === COMMONLY USED ===
    language: str = Query("en", description="Language code", example="en"),
    country: str = Query("US", description="Country code", example="US"),
    max_results: int = Query(5, ge=1, le=100, description="Max results (1-100)"),
    # === DATE FILTERS ===
    start_date: Optional[str] = Query(None, regex=r"^\d{4}-\d{2}-\d{2}$", description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, regex=r"^\d{4}-\d{2}-\d{2}$", description="End date (YYYY-MM-DD)"),
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
            exclude_duplicates=exclude_duplicates
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
                empty_detail=(
                    f"No processable news found for the location '{location}' "
                    "after URL decoding."
                ),
            )

        # Get cached result or fetch and cache (10 minute TTL for location news)
        return await get_cached_or_fetch(cache_key, fetch_location_news, ttl=600)
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error fetching Google News for location '{location}': {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

# The `/source/` endpoint definition is already provided above.

@gnews_router.get("/articles/", summary="Bulk Articles", response_model=NewsResponse, response_model_exclude_none=True)
async def get_google_news_articles(
    # === COMMONLY USED ===
    query: str = Query("news", description="Search query", example="technology"),
    language: str = Query("en", description="Language code", example="en"),
    country: str = Query("US", description="Country code", example="US"),
    max_results: int = Query(5, ge=1, le=100, description="Max results (1-100)"),
    # === TIME PERIOD ===
    period: str = Query("1d", regex=r"^\d+[dwmy]$", description="Period: 7d, 1w, 1m, 1y"),
    # === AUTH ===
    rate_limit_check: None = Depends(rate_limit),
):
    """Get bulk news articles over a time period."""
    try:
        # Generate cache key
        cache_key = generate_cache_key(
            "gnews:articles",
            query=query,
            language=language,
            country=country,
            max_results=max_results,
            period=period
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
                empty_detail=(
                    "No processable articles found for the given parameters "
                    "after URL decoding."
                ),
            )

        # Get cached result or fetch and cache (10 minute TTL for bulk articles)
        return await get_cached_or_fetch(cache_key, fetch_articles, ttl=600)

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error fetching Google News articles for query '{query}': {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

# -----------------------------------------------------------------------------
# Outbound fetch policy for /article-details/
#
# This endpoint takes a URL from the caller and fetches it from inside the
# container network, which is a textbook SSRF sink. Everything it is allowed
# to reach is listed here, and the list is deliberately empty of wildcards:
# an allow-list that has to be widened on purpose beats a deny-list that has
# to enumerate every internal range correctly, forever.
#
# Operators extend it with NEWS_ARTICLE_ALLOWED_HOSTS, a comma-separated list
# of publisher hosts (a leading dot matches subdomains, e.g. ".bbc.co.uk").
# Until they do, only Google News itself is reachable.
# -----------------------------------------------------------------------------
def _configured_article_hosts() -> Tuple[str, ...]:
    """Read the operator-supplied publisher allow-list from the environment."""
    raw = os.environ.get("NEWS_ARTICLE_ALLOWED_HOSTS", "")
    return tuple(entry.strip().lower() for entry in raw.split(",") if entry.strip())


ARTICLE_DETAILS_ALLOWED_HOSTS: Tuple[str, ...] = (
    NEWS_ALLOWED_HOSTS + _configured_article_hosts()
)

# Plaintext HTTP is off by default: a downgraded fetch is both interceptable
# and a convenient way to reach internal services that never speak TLS.
ARTICLE_DETAILS_ALLOW_HTTP: bool = (
    os.environ.get("NEWS_ARTICLE_ALLOW_HTTP", "").strip().lower() in {"1", "true", "yes"}
)

# Resolve and check every address the host maps to. Only unit tests that must
# not touch the network set this to False.
ARTICLE_DETAILS_RESOLVE_DNS: bool = True

# One message for every rejection. "Connection refused" vs "timed out" vs
# "not on the allow-list" is exactly what turned this endpoint into an
# internal port-scan oracle; identical bodies remove that signal.
BLOCKED_URL_DETAIL = "The supplied URL is not permitted."
ARTICLE_FETCH_FAILED_DETAIL = "Could not retrieve the requested article."


# A redirect chain is bounded: each hop is another outbound request, and an
# unbounded chain is a cheap way to keep a worker busy.
ARTICLE_MAX_REDIRECTS = 3

# Cap on the decompressed article body. Generous for a news page, and small
# enough that one request cannot exhaust the worker's memory.
ARTICLE_MAX_BYTES = 8 * 1024 * 1024


def validate_article_url(raw_url: str):
    """Run a URL through the shared guard with this endpoint's policy."""
    return validate_outbound_url(
        raw_url,
        allowed_hosts=ARTICLE_DETAILS_ALLOWED_HOSTS,
        allow_http=ARTICLE_DETAILS_ALLOW_HTTP,
        resolve_dns=ARTICLE_DETAILS_RESOLVE_DNS,
    )


async def fetch_allow_listed_html(validated_url: str) -> Tuple[str, str]:
    """Fetch ``validated_url``, re-validating every redirect it is sent on.

    Validating once and then handing the URL to a library that follows
    redirects checks only the first hop: an allow-listed host that answers
    ``302 Location: http://169.254.169.254/`` sends the *library's* request
    somewhere the guard never saw. Redirects are therefore not followed
    automatically; each ``Location`` goes back through the same validation as
    the caller's original URL.

    The body is streamed and capped at ``ARTICLE_MAX_BYTES``. Reading
    ``response.text`` in one go would buffer whatever the far end sends, and
    the cap has to be applied to the *decompressed* stream: a few kilobytes of
    gzip can expand to gigabytes, so a Content-Length check is not enough.

    Returns:
        ``(html, final_url)`` -- the body, and the URL it actually came from.

    Raises:
        UrlNotAllowed: if any hop fails validation, or the body is too large.
        httpx.HTTPError: on a transport or status failure.

    Known residual risk: between validation and connect, a hostile DNS server
    could swap a public answer for a private one (rebinding). Closing that
    needs connection-level pinning to ``ValidatedUrl.ip_addresses``, which
    httpx cannot express without a custom transport. The window is narrow
    here because the host must already be on an operator-managed allow-list.
    A configured outbound proxy resolves the host itself, which makes the
    guard's DNS check advisory on that path; the scheme, host and port checks
    still apply.
    """
    proxy_url = await get_proxy()
    client = await get_gnews_http_client(proxy_url=proxy_url)

    current = validated_url
    for _ in range(ARTICLE_MAX_REDIRECTS + 1):
        async with client.stream(
            "GET",
            current,
            follow_redirects=False,
            timeout=settings.HTTP_READ_TIMEOUT,
            headers={"User-Agent": USER_AGENTS["windows_chrome"]},
        ) as response:
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise UrlNotAllowed("redirect without a Location header")

                # Relative redirects resolve against the current URL, so
                # validating the joined result means a relative hop cannot
                # smuggle in a new host. A hop to another scheme is validated
                # like any other: only https (http if explicitly enabled).
                next_url = str(httpx.URL(current).join(location))
                current = validate_article_url(next_url).url
                continue

            response.raise_for_status()

            chunks = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > ARTICLE_MAX_BYTES:
                    raise UrlNotAllowed(
                        f"response body exceeded {ARTICLE_MAX_BYTES} bytes"
                    )
                chunks.append(chunk)

            encoding = response.charset_encoding or "utf-8"
            return b"".join(chunks).decode(encoding, errors="replace"), current

    raise UrlNotAllowed(f"redirect chain exceeded {ARTICLE_MAX_REDIRECTS} hops")


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
        raise HTTPException(status_code=400, detail=exc.public_message)

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
            except LookupError as le:  # Specific exception for NLTK resource not found
                logger.warning("NLTK resource not found for %s: %s", validated.host, le)
                nlp_success = False

            # Build response (convert publish_date to string for JSON serialization)
            publish_date_str = None
            if article.publish_date:
                publish_date_str = article.publish_date.isoformat() if hasattr(article.publish_date, 'isoformat') else str(article.publish_date)

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
                "meta_keywords": article.meta_keywords
            }

            if nlp_success:
                response_data.update({
                    "summary": article.summary,
                    "keywords": article.keywords
                })
            else:
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
        raise HTTPException(status_code=502, detail=ARTICLE_FETCH_FAILED_DETAIL)
    except ArticleException as ae:
        # The old body echoed the target host and the underlying failure, which
        # let a caller tell "port closed" from "port open but not HTML" and use
        # the endpoint as an internal port scanner. Cause goes to the logs only.
        logger.error("Newspaper error fetching %s: %s", validated.host, ae)
        raise HTTPException(status_code=502, detail=ARTICLE_FETCH_FAILED_DETAIL)
    except Exception as e:
        logger.error(
            "Unexpected error fetching article details for %s: %s",
            validated.host, e, exc_info=True,
        )
        raise HTTPException(status_code=502, detail=ARTICLE_FETCH_FAILED_DETAIL)
