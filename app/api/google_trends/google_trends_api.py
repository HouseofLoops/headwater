"""
Google Trends API Router.

Provides endpoints for Google Trends data including trending topics,
interest over time, and related queries.
"""

import asyncio
import json
import logging
import random
import re
from datetime import date

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from trendspy import BatchPeriod, Trends
from trendspy.client import TrendsQuotaExceededError
from trendspy.news_article import NewsArticle

from app.core.cache_manager import generate_cache_key, get_cached_or_fetch
from app.core.constants import REFERER_LIST, USER_AGENT_LIST
from app.core.exceptions import UpstreamRateLimitedError, parse_retry_after
from app.core.log_safety import scrub
from app.core.proxy import get_proxy, mask_proxy
from app.core.rate_limiter import rate_limit
from app.schemas.enums import (
    HumanFriendlyBatchPeriod,
)


# Pydantic model for date range
class DateRangeTimeframeModel(BaseModel):
    start_date: date = Field(..., description="Start date in YYYY-MM-DD format.")
    end_date: date | None = Field(None, description="End date in YYYY-MM-DD format.")


# Create the router
google_trends_router = APIRouter()
logger = logging.getLogger("uvicorn")
logging.basicConfig(level=logging.DEBUG)


# -------------------------------------------------------------------------
# Utility Functions
# -------------------------------------------------------------------------
def df_to_json(df: pd.DataFrame):
    """
    Convert a Pandas DataFrame to a list of dictionaries.
    If df is empty, return an empty list.
    """
    if df.empty:
        return []
    return df.reset_index(drop=True).to_dict(orient="records")


def to_jsonable(value):
    """
    Recursively convert objects to JSON-serializable types:
    - Pandas DataFrames -> list of dicts
    - Numpy int/float  -> Python int/float
    - Numpy arrays     -> lists
    - dict/list        -> recursively process
    """
    if isinstance(value, pd.DataFrame):
        return df_to_json(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_jsonable(x) for x in value]
    return value


# -------------------------------------------------------------------------
# Header Configuration - imported from app.core.constants
# REFERER_LIST and USER_AGENT_LIST are used for header rotation
# -------------------------------------------------------------------------


def get_random_headers():
    """
    Selects a random referer and user-agent from predefined lists.
    Returns a dictionary of headers.
    """
    referer = random.choice(REFERER_LIST)  # nosec B311 - header rotation, not security
    user_agent = random.choice(USER_AGENT_LIST)  # nosec B311
    headers = {
        "Referer": referer,
        "User-Agent": user_agent,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }
    return headers


# -------------------------------------------------------------------------
# Timeframe mapping
#
# Built once at import rather than per request. The completeness check below
# turns a wrong or missing enum member into an immediate, loud import failure
# instead of a 500 that only shows up when somebody calls the endpoint -- the
# exact shape of the bug that left /trending-now-showcase-timeline broken (the
# router referenced ``HumanFriendlyBatchPeriod.past_4h`` when the enum only
# defined ``PAST_4H``).
# -------------------------------------------------------------------------
BATCH_PERIOD_BY_TIMEFRAME = {
    HumanFriendlyBatchPeriod.past_4h: BatchPeriod.Past4H,
    HumanFriendlyBatchPeriod.past_24h: BatchPeriod.Past24H,
    HumanFriendlyBatchPeriod.past_48h: BatchPeriod.Past48H,
    HumanFriendlyBatchPeriod.past_7d: BatchPeriod.Past7D,
}

_unmapped_timeframes = set(HumanFriendlyBatchPeriod) - set(BATCH_PERIOD_BY_TIMEFRAME)
if _unmapped_timeframes:
    raise RuntimeError(
        "BATCH_PERIOD_BY_TIMEFRAME is missing HumanFriendlyBatchPeriod members: "
        + ", ".join(sorted(member.value for member in _unmapped_timeframes))
    )


# -------------------------------------------------------------------------
# Upstream failure handling
#
# Every endpoint below used to wrap its trendspy call in a bare
# ``except Exception: return None`` and then answer ``{"data": []}`` with
# HTTP 200. Because that 200 went through ``get_cached_or_fetch``, a single
# upstream blip was written into the cache and served to every caller for the
# full TTL. Two distinct situations were collapsed into one response:
#
#   * Google Trends answered, and the answer was empty  -> 200, "no data"
#   * The call to Google Trends failed                  -> must be 502
#
# ``run_trends_call`` keeps them apart. It returns the upstream result (which
# may legitimately be empty) and raises ``UpstreamUnavailable`` when the call
# itself failed. ``get_cached_or_fetch`` re-raises rather than caching, so a
# failure is never stored.
# -------------------------------------------------------------------------

# Identical for every cause, so the response body cannot be used to probe what
# went wrong upstream. The detail lives in the logs.
UPSTREAM_UNAVAILABLE_DETAIL = "Upstream Google Trends request failed. Please retry."
UPSTREAM_UNUSABLE_DETAIL = "Upstream Google Trends returned an unusable response."
UPSTREAM_REJECTED_DETAIL = (
    "Google Trends rejected the request as invalid (HTTP 400). Retrying it unchanged will not help."
)
NEWS_TOKENS_REJECTED_DETAIL = (
    "Google Trends rejected the news tokens. Send the numeric IDs, or the [id, language, geo] "
    "tokens exactly as /trending-now returns them."
)

# Named in the 429 body and log line so a client can tell which upstream is throttling.
GOOGLE_TRENDS = "Google Trends"


class UpstreamUnavailable(Exception):
    """Raised when a call to Google Trends fails or returns something unusable.

    Distinct from "Google Trends returned nothing": this means we never got a
    usable answer, so the result must not be cached and must not be presented
    to the caller as a successful empty response.

    Attributes:
        operation: Name of the trendspy call, for logs.
        detail: Message safe to return in the HTTP response body.
    """

    def __init__(self, operation: str, detail: str = UPSTREAM_UNAVAILABLE_DETAIL):
        super().__init__(f"{operation}: {detail}")
        self.operation = operation
        self.detail = detail


class UpstreamRateLimited(UpstreamUnavailable):
    """Google Trends is throttling us: HTTP 429, or trendspy's quota error.

    The classification :func:`run_trends_call` turns into a 429
    ``UpstreamRateLimitedError`` with ``Retry-After``, rather than a 502: the
    upstream is healthy and asked us to wait.

    Attributes:
        retry_after: Seconds from Google's own ``Retry-After`` header, or
            ``None`` when it sent none (the config default is used then).
    """

    def __init__(self, operation: str, retry_after: int | None = None):
        super().__init__(operation, "Google Trends is rate limiting requests.")
        self.retry_after = retry_after


class UpstreamRejected(UpstreamUnavailable):
    """Google Trends answered HTTP 400, or refused the RPC arguments.

    Still a 502, but with a detail that does not invite a retry: the same
    request will be rejected again. /trending-now-showcase-timeline is the
    standing example -- Google now rejects every request trendspy 0.1.6
    builds for it.
    """

    def __init__(self, operation: str, detail: str = UPSTREAM_REJECTED_DETAIL):
        super().__init__(operation, detail)


def _upstream_response(exc: BaseException):
    """Return the ``requests`` response behind ``exc`` or its causes, if any.

    ``requests.HTTPError`` carries the response (status and headers). trendspy
    raises one from ``_get`` once its own retries are spent, and
    :class:`HeadwaterTrends` raises one for failed batchexecute calls.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        response = getattr(current, "response", None)
        if isinstance(getattr(response, "status_code", None), int):
            return response
        current = current.__cause__ or current.__context__
    return None


def classify_trends_failure(operation: str, exc: BaseException) -> UpstreamUnavailable:
    """Map an exception raised by a trendspy call to the failure it represents.

    * ``TrendsQuotaExceededError`` or HTTP 429 -> :class:`UpstreamRateLimited` (429)
    * HTTP 400                                -> :class:`UpstreamRejected` (502)
    * anything else                           -> :class:`UpstreamUnavailable` (502)

    trendspy's quota error is raised by ``related_queries`` and
    ``related_topics`` when Google marks the embed token
    ``USER_TYPE_EMBED_OVER_QUOTA``; it has no HTTP status or ``Retry-After``.
    """
    if isinstance(exc, TrendsQuotaExceededError):
        return UpstreamRateLimited(operation)

    response = _upstream_response(exc)
    status = response.status_code if response is not None else None
    if status == 429:
        return UpstreamRateLimited(operation, parse_retry_after(response.headers.get("Retry-After")))
    if status == 400:
        return UpstreamRejected(operation)
    return UpstreamUnavailable(operation)


class HeadwaterTrends(Trends):
    """trendspy's client, made to fail loudly on a failed batchexecute call.

    trendspy 0.1.6 is the latest release (December 2024) and is no longer
    updated, so this is fixed here rather than upstream. Its ``_get_batch``
    never checks the status code: a 429 or 400 from the batchexecute endpoint
    (trending now, news by IDs, showcase timeline) only surfaces later as a
    ``ValueError("Invalid response: status 429 ...")`` that has dropped the
    response, so the status and ``Retry-After`` were lost and every such
    failure looked the same. Raising ``HTTPError`` here keeps the response
    attached for :func:`classify_trends_failure`.
    """

    def _get_batch(self, req_id, data):
        response = super()._get_batch(req_id, data)
        if response.status_code >= 400:
            response.raise_for_status()
        return response


# /geo and /categories are reference data: 3681 locations and 1133 categories that
# change on the order of months. The default TTL is tuned for trend series, which
# move hourly, so these were re-fetched from Google far more often than the data
# can possibly change.
REFERENCE_DATA_TTL_SECONDS = 24 * 60 * 60


def flatten_geo_tree(node, out=None):
    """Flatten Google's geo tree into ``[{"name": ..., "id": ...}, ...]``.

    trendspy fetches and parses this tree correctly, then reads
    ``HierarchicalIndex.name_to_location``, an attribute that does not exist in
    0.1.6 -- every /geo call raised AttributeError and surfaced as a 502. 0.1.6
    is the newest release (December 2024), so there is no version to upgrade to.

    The parsed payload is a plain ``{"name", "id", "children"}`` tree, so we walk
    it here instead of depending on that accessor. Nodes without an id are
    grouping levels and contribute only their children.
    """
    if out is None:
        out = []
    if isinstance(node, dict):
        if node.get("id"):
            out.append({"name": node.get("name"), "id": node["id"]})
        for child in node.get("children") or ():
            flatten_geo_tree(child, out)
    elif isinstance(node, (list, tuple)):
        for child in node:
            flatten_geo_tree(child, out)
    return out


def fetch_geo_locations(trends_obj, find=None):
    """Return Google Trends locations, optionally filtered by substring."""
    from trendspy.client import API_GEO_DATA_URL

    raw = trends_obj._get(API_GEO_DATA_URL, {"hl": trends_obj.language, "tz": trends_obj.tzs})
    rows = flatten_geo_tree(trends_obj._parse_protected_json(raw))
    if find:
        needle = find.strip().lower()
        rows = [r for r in rows if needle in (r["name"] or "").lower() or needle in (r["id"] or "").lower()]
    return rows


def trend_kwargs(**kwargs):
    """Drop unset options so trendspy applies its own defaults.

    Its signatures default to ``geo=''``, ``cat=0`` and ``gprop=''`` -- not
    ``None``. These endpoints declare the same options as ``Query(None)``, so an
    unset option arrived as ``None`` and was forwarded verbatim, which is not a
    value trendspy builds a valid request from. Omitting the key is the only
    thing that reproduces "the caller did not ask for this option".
    """
    return {k: v for k, v in kwargs.items() if v is not None}


async def run_trends_call(operation: str, call):
    """Run a blocking trendspy call off the event loop.

    Args:
        operation: Name used in log messages (e.g. ``"interest_over_time"``).
        call: Zero-argument callable performing the trendspy request.

    Returns:
        Whatever trendspy returned, including empty results.

    Raises:
        UpstreamRateLimitedError: Google is rate limiting us (HTTP 429 or
            trendspy's quota error). An ``HTTPException``, so it passes
            through the cache (uncached) and every handler as a 429.
        UpstreamUnavailable: any other failure, classified by
            :func:`classify_trends_failure` (rejected or failed). The original
            exception is logged, never returned to the caller.
    """
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, call)
    except UpstreamUnavailable:
        # Already classified by a Headwater helper running in the executor.
        raise
    except Exception as exc:
        failure = classify_trends_failure(operation, exc)
        if isinstance(failure, UpstreamRateLimited):
            # Expected and transient: a warning without a traceback, not an
            # internal error. Google throttles Trends hard.
            logger.warning("Google Trends rate-limited %s (%s)", operation, type(exc).__name__)
            raise UpstreamRateLimitedError(GOOGLE_TRENDS, retry_after=failure.retry_after) from exc
        if isinstance(failure, UpstreamRejected):
            logger.error("Google Trends rejected %s with HTTP 400: %s", operation, exc)
        else:
            logger.error("Google Trends call %s failed: %s", operation, exc, exc_info=True)
        raise failure from exc


async def cached_trends_response(cache_key: str, fetch_func, ttl: int | None = None):
    """Serve a Trends endpoint from cache, mapping upstream failure to 502.

    Rate limiting never reaches here as ``UpstreamUnavailable``:
    :func:`run_trends_call` raises it as a 429 ``UpstreamRateLimitedError``.
    ``get_cached_or_fetch`` re-raises instead of caching when ``fetch_func``
    raises, so an upstream failure leaves the cache untouched and the next
    request tries again.
    """
    try:
        return await get_cached_or_fetch(cache_key, fetch_func, ttl=ttl)
    except UpstreamUnavailable as exc:
        raise HTTPException(status_code=502, detail=exc.detail) from exc


def is_empty_result(value) -> bool:
    """Return True when the upstream answer carries no rows.

    Empty is a legitimate answer (a keyword nobody searched for), so it is
    reported as 200 with an explanatory message -- never as a failure.

    ``None`` is counted as empty deliberately: trendspy returns it for "no
    data", and every way of *failing* now raises instead (see
    :func:`run_trends_call`), so ``None`` no longer doubles as a swallowed
    error the way it did when each endpoint caught ``Exception`` and returned
    ``None`` itself. If a future trendspy release starts returning ``None``
    for errors as well, this is the line that has to change.
    """
    if value is None:
        return True
    if isinstance(value, pd.DataFrame):
        return value.empty
    if isinstance(value, (list, tuple, dict, str, set)):
        return len(value) == 0
    return False


def encode_trends_payload(operation: str, raw):
    """Convert an upstream result into a JSON-serialisable payload.

    Raises:
        UpstreamUnavailable: if the result cannot be encoded. Returning
            ``{"data": []}`` here would cache an unusable upstream answer as a
            success, which is the failure mode this module exists to avoid.
    """
    try:
        return jsonable_encoder(to_jsonable(raw))
    except Exception as exc:
        logger.error(
            "Could not serialise Google Trends %s response: %s",
            operation,
            exc,
            exc_info=True,
        )
        raise UpstreamUnavailable(operation, UPSTREAM_UNUSABLE_DETAIL) from exc


# -------------------------------------------------------------------------
# News by IDs
#
# Google's news RPC (batchexecute ``w4opAf``) takes each token as
# ``[numeric id, language, geo]``, the shape /trending-now returns in
# ``news_tokens``. The endpoint used to split its input on commas and forward
# bare strings. Google answers those with HTTP 200, a null payload and RPC
# status [3] (INVALID_ARGUMENT), and trendspy's ``trending_now_news_by_ids``
# then calls ``json.loads(None)``: "the JSON object must be str, bytes or
# bytearray, not NoneType". trendspy also crashes on a well-formed token Google
# does not know (payload ``"[]"`` -> IndexError), and on success it returns
# ``NewsArticle`` objects, which the old normaliser rejected as an unexpected
# shape, so the endpoint could not answer 200 at all. The RPC is therefore
# called here, not through trendspy's method.
# -------------------------------------------------------------------------
TRENDING_NEWS_RPC_ID = "w4opAf"
MAX_NEWS_TOKENS = 50
MAX_NEWS_ARTICLES = 50
# The language /trending-now's tokens carry (trendspy's default).
NEWS_TOKEN_LANGUAGE = "en"  # nosec B105 - a language code, not a secret
_NEWS_TOKEN_ID_RE = re.compile(r"^\d{1,20}$")
_NEWS_TOKEN_LANGUAGE_RE = re.compile(r"^[a-z]{2,3}$")
_NEWS_TOKEN_GEO_RE = re.compile(r"^[A-Z]{2}(-[A-Z0-9]{1,3})?$")
NEWS_TOKENS_FORMAT_HINT = (
    "Send comma-separated numeric IDs (e.g. 4830466997,4830466998) or the JSON array "
    '/trending-now returns in news_tokens (e.g. [[4830466997,"en","US"]]).'
)


class InvalidNewsTokens(ValueError):
    """``news_tokens`` cannot be turned into Google's token shape.

    The message is safe to return to the caller: it never echoes the input.
    """


def _news_token_id(value, position: int) -> int:
    # bool is an int subclass; true/false in the JSON form is not an ID.
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if isinstance(value, str) and _NEWS_TOKEN_ID_RE.match(value.strip()):
        return int(value.strip())
    raise InvalidNewsTokens(f"news_tokens item {position} is not a numeric ID. {NEWS_TOKENS_FORMAT_HINT}")


def parse_news_tokens(raw: str, geo: str) -> list[list]:
    """Turn the ``news_tokens`` query value into ``[[id, language, geo], ...]``.

    Accepts either form a client can get from /trending-now:

    * comma-separated numeric IDs (``4830466997,4830466998``), completed with
      language ``en`` and ``geo``;
    * the ``news_tokens`` JSON array as returned (``[[4830466997,"en","US"]]``).

    Duplicates are dropped, first occurrence kept.

    Args:
        raw: The ``news_tokens`` query value.
        geo: Location used to complete bare IDs.

    Returns:
        The tokens in the shape Google's news RPC accepts.

    Raises:
        InvalidNewsTokens: empty input, bad JSON, a malformed token, or more
            than ``MAX_NEWS_TOKENS`` tokens.
    """
    raw = raw.strip()
    geo = geo.strip().upper()
    if not _NEWS_TOKEN_GEO_RE.match(geo):
        raise InvalidNewsTokens("geo must be a location code such as US or GB.")
    tokens: list[list] = []

    if raw.startswith("["):
        try:
            decoded = json.loads(raw)
        except ValueError as exc:
            raise InvalidNewsTokens(f"news_tokens is not valid JSON. {NEWS_TOKENS_FORMAT_HINT}") from exc
        if not isinstance(decoded, list):
            raise InvalidNewsTokens(f"news_tokens must be a JSON array. {NEWS_TOKENS_FORMAT_HINT}")
        for position, item in enumerate(decoded, start=1):
            if not isinstance(item, list) or len(item) != 3:
                raise InvalidNewsTokens(
                    f"news_tokens item {position} must be [id, language, geo]. {NEWS_TOKENS_FORMAT_HINT}"
                )
            token_id, language, token_geo = item
            if not isinstance(language, str) or not _NEWS_TOKEN_LANGUAGE_RE.match(language.lower()):
                raise InvalidNewsTokens(f"news_tokens item {position} has an invalid language code.")
            if not isinstance(token_geo, str) or not _NEWS_TOKEN_GEO_RE.match(token_geo.upper()):
                raise InvalidNewsTokens(f"news_tokens item {position} has an invalid geo code.")
            tokens.append([_news_token_id(token_id, position), language.lower(), token_geo.upper()])
    else:
        parts = [part.strip() for part in raw.split(",") if part.strip()]
        tokens = [[_news_token_id(part, position), NEWS_TOKEN_LANGUAGE, geo] for position, part in enumerate(parts, 1)]

    # A set, not `token not in unique`: the input is caller-sized (up to the
    # URL limit), and a quadratic scan would run before the count check.
    seen: set[tuple] = set()
    unique: list[list] = []
    for token in tokens:
        if tuple(token) not in seen:
            seen.add(tuple(token))
            unique.append(token)

    if not unique:
        raise InvalidNewsTokens("No valid news tokens provided.")
    if len(unique) > MAX_NEWS_TOKENS:
        raise InvalidNewsTokens(f"news_tokens carries more than {MAX_NEWS_TOKENS} tokens.")
    return unique


def fetch_trending_news(trends_obj, tokens: list[list], max_news: int) -> list[dict]:
    """Fetch the news articles for Trending Now news tokens.

    Calls the RPC trendspy's ``trending_now_news_by_ids`` wraps, but handles
    the answers that method crashes on (see the section comment above).

    Args:
        trends_obj: A :class:`HeadwaterTrends` instance.
        tokens: ``[[id, language, geo], ...]`` from :func:`parse_news_tokens`.
        max_news: Maximum number of articles Google should return.

    Returns:
        Articles as ``{"title", "url", "source", "picture", "time", "snippet"}``
        dicts, the fields /trending-now uses for its own news. Empty when
        Google has no news for the tokens (unknown or expired IDs).

    Raises:
        UpstreamRejected: Google refused the tokens (null payload).
        UpstreamUnavailable: the response is not in the shape we know.
        requests.HTTPError: the batchexecute call failed (429, 400, ...);
            :func:`run_trends_call` classifies it.
    """
    operation = "trending_now_news_by_ids"
    response = trends_obj._get_batch(TRENDING_NEWS_RPC_ID, [tokens, max_news])
    try:
        envelope = trends_obj._parse_protected_json(response)
    except ValueError as exc:
        logger.error("Google Trends %s response could not be parsed: %s", operation, exc)
        raise UpstreamUnavailable(operation, UPSTREAM_UNUSABLE_DETAIL) from exc

    entry = None
    if isinstance(envelope, list):
        entry = next(
            (
                item
                for item in envelope
                if isinstance(item, list) and len(item) > 2 and item[:2] == ["wrb.fr", TRENDING_NEWS_RPC_ID]
            ),
            None,
        )
    if entry is None:
        logger.error("Google Trends %s response carries no %s result", operation, TRENDING_NEWS_RPC_ID)
        raise UpstreamUnavailable(operation, UPSTREAM_UNUSABLE_DETAIL)

    payload = entry[2]
    if payload is None:
        # Google's way of refusing the arguments; entry[5] is the RPC status,
        # e.g. [3] for INVALID_ARGUMENT. This is what trendspy fed to json.loads.
        rpc_status = entry[5] if len(entry) > 5 else None
        logger.error("Google Trends refused the %s arguments (RPC status %s)", operation, rpc_status)
        raise UpstreamRejected(operation, NEWS_TOKENS_REJECTED_DETAIL)

    try:
        decoded = json.loads(payload)
    except (TypeError, ValueError) as exc:
        logger.error("Google Trends %s payload is not JSON: %s", operation, exc)
        raise UpstreamUnavailable(operation, UPSTREAM_UNUSABLE_DETAIL) from exc

    # "[]" when Google knows none of the tokens; "[[article, ...]]" otherwise.
    if decoded in ([], [[]], [None]):
        return []
    if not isinstance(decoded, list) or not isinstance(decoded[0], list):
        logger.error("Google Trends %s payload has an unexpected shape", operation)
        raise UpstreamUnavailable(operation, UPSTREAM_UNUSABLE_DETAIL)

    try:
        articles = [NewsArticle.from_api(item) for item in decoded[0]]
    except (TypeError, ValueError, IndexError, KeyError, AttributeError) as exc:
        logger.error("Google Trends %s article has an unexpected shape: %s", operation, exc)
        raise UpstreamUnavailable(operation, UPSTREAM_UNUSABLE_DETAIL) from exc

    return [
        {
            "title": article.title,
            "url": article.url,
            "source": article.source,
            "picture": article.picture,
            "time": article.time,
            "snippet": article.snippet,
        }
        for article in articles
    ]


def empty_trends_response(message: str) -> dict:
    """Build the 200 response used when Google Trends genuinely had no data."""
    return {"data": [], "message": message}


# -------------------------------------------------------------------------
# Helper: Create a new Trends instance per request
# -------------------------------------------------------------------------
async def get_trends_instance():
    """
    Create and return a Trends instance, applying proxy if needed and random headers.

    The instance is a :class:`HeadwaterTrends`, so a failed batchexecute call
    keeps its HTTP status and ``Retry-After``.
    """
    proxy_url = await get_proxy()
    headers = get_random_headers()
    if proxy_url:
        logger.debug("TrendSpy is using proxy: %s", mask_proxy(proxy_url))
        return HeadwaterTrends(proxy=proxy_url, headers=headers)
    else:
        logger.debug("TrendSpy is not using any proxy.")
        return HeadwaterTrends(headers=headers)


# -------------------------------------------------------------------------
# 1) Interest Over Time
# -------------------------------------------------------------------------
@google_trends_router.get("/interest-over-time", summary="Interest Over Time")
async def interest_over_time(
    # === REQUIRED ===
    keywords: str = Query(..., description="Comma-separated keywords", examples=["python,javascript"]),
    # === COMMONLY USED ===
    timeframe: str = Query("today 12-m", description="Time range: now 1-H, now 4-H, today 1-m, today 3-m, today 12-m"),
    geo: str | None = Query(None, description="Location code (US, US-NY, GB)", examples=["US"]),
    # === FILTERS ===
    cat: str | None = Query(None, description="Category ID (e.g., 13=Computers)"),
    gprop: str | None = Query(None, description="Property: images, youtube, news, froogle"),
    # === AUTH ===
    rate_limit: None = Depends(rate_limit),
):
    """Get search interest over time for keywords."""
    try:
        kw_list = [kw.strip() for kw in keywords.split(",") if kw.strip()]
        if not kw_list:
            raise HTTPException(status_code=400, detail="No valid keywords provided.")

        # Generate cache key
        cache_key = generate_cache_key(
            "trends_interest_over_time", keywords=keywords, timeframe=timeframe, geo=geo, cat=cat, gprop=gprop
        )

        async def fetch_interest_over_time():
            trends_obj = await get_trends_instance()

            raw_results = await run_trends_call(
                "interest_over_time",
                lambda: trends_obj.interest_over_time(
                    kw_list,
                    **trend_kwargs(timeframe=timeframe, geo=geo, cat=cat, gprop=gprop),
                ),
            )

            if is_empty_result(raw_results):
                logger.info("Google Trends returned no interest_over_time rows")
                return empty_trends_response("No data returned from Google Trends.")

            return {"data": encode_trends_payload("interest_over_time", raw_results)}

        # Get cached result or fetch and cache. An upstream failure raises out
        # of the fetcher, so nothing is written to the cache.
        return await cached_trends_response(cache_key, fetch_interest_over_time)

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error in interest_over_time: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error") from e


# -------------------------------------------------------------------------
# 2) Interest By Region
# -------------------------------------------------------------------------
@google_trends_router.get("/interest-by-region", summary="Interest By Region")
async def interest_by_region(
    # === REQUIRED ===
    keyword: str = Query(..., description="Single keyword", examples=["python"]),
    # === COMMONLY USED ===
    geo: str | None = Query(None, description="Location code (US, GB)", examples=["US"]),
    resolution: str = Query("COUNTRY", description="Detail level: COUNTRY, REGION, CITY, DMA"),
    timeframe: str = Query("today 12-m", description="Time range"),
    # === FILTERS ===
    cat: str | None = Query(None, description="Category ID"),
    # === AUTH ===
    rate_limit: None = Depends(rate_limit),
):
    """Get geographic breakdown of search interest."""
    try:
        # Generate cache key
        cache_key = generate_cache_key(
            "trends_interest_by_region", keyword=keyword, timeframe=timeframe, geo=geo, cat=cat, resolution=resolution
        )

        async def fetch_interest_by_region():
            trends_obj = await get_trends_instance()

            raw_results = await run_trends_call(
                "interest_by_region",
                lambda: trends_obj.interest_by_region(
                    keyword,
                    **trend_kwargs(timeframe=timeframe, geo=geo, cat=cat, resolution=resolution),
                ),
            )

            if is_empty_result(raw_results):
                logger.info("Google Trends returned no interest_by_region rows")
                return empty_trends_response("No data returned from Google Trends.")

            return {"data": encode_trends_payload("interest_by_region", raw_results)}

        # Get cached result or fetch and cache
        return await cached_trends_response(cache_key, fetch_interest_by_region)

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error in interest_by_region: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error") from e


# -------------------------------------------------------------------------
# 3) Related Queries (Uses a Custom Referer in the Headers)
# -------------------------------------------------------------------------
@google_trends_router.get("/related-queries", summary="Related Queries")
async def related_queries(
    # === REQUIRED ===
    keyword: str = Query(..., description="Single keyword", examples=["python"]),
    # === COMMONLY USED ===
    geo: str | None = Query(None, description="Location code (US, GB)", examples=["US"]),
    timeframe: str = Query("today 12-m", description="Time range"),
    # === FILTERS ===
    cat: str | None = Query(None, description="Category ID"),
    gprop: str | None = Query(None, description="Property: images, youtube, news, froogle"),
    # === AUTH ===
    rate_limit: None = Depends(rate_limit),
):
    """Get related search queries (rising and top)."""
    try:
        # Generate cache key
        cache_key = generate_cache_key(
            "trends_related_queries", keyword=keyword, timeframe=timeframe, geo=geo, cat=cat, gprop=gprop
        )

        async def fetch_related_queries():
            trends_obj = await get_trends_instance()

            raw_results = await run_trends_call(
                "related_queries",
                lambda: trends_obj.related_queries(
                    keyword,
                    **trend_kwargs(timeframe=timeframe, geo=geo, cat=cat, gprop=gprop),
                ),
            )

            if is_empty_result(raw_results):
                logger.info("Google Trends returned no related_queries rows")
                return empty_trends_response("No related queries data was returned.")

            return {"data": encode_trends_payload("related_queries", raw_results)}

        # Get cached result or fetch and cache
        return await cached_trends_response(cache_key, fetch_related_queries)

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error in related_queries: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error") from e


# -------------------------------------------------------------------------
# 4) Related Topics
# -------------------------------------------------------------------------
@google_trends_router.get("/related-topics", summary="Related Topics")
async def related_topics(
    # === REQUIRED ===
    keyword: str = Query(..., description="Single keyword", examples=["python"]),
    # === COMMONLY USED ===
    geo: str | None = Query(None, description="Location code (US, GB)", examples=["US"]),
    timeframe: str = Query("today 12-m", description="Time range"),
    # === FILTERS ===
    cat: str | None = Query(None, description="Category ID"),
    gprop: str | None = Query(None, description="Property: images, youtube, news, froogle"),
    # === AUTH ===
    rate_limit: None = Depends(rate_limit),
):
    """Get related topics (rising and top)."""
    try:
        # Generate cache key
        cache_key = generate_cache_key(
            "trends_related_topics", keyword=keyword, timeframe=timeframe, geo=geo, cat=cat, gprop=gprop
        )

        async def fetch_related_topics():
            trends_obj = await get_trends_instance()

            raw_results = await run_trends_call(
                "related_topics",
                lambda: trends_obj.related_topics(
                    keyword,
                    **trend_kwargs(timeframe=timeframe, geo=geo, cat=cat, gprop=gprop),
                ),
            )

            if is_empty_result(raw_results):
                logger.info("Google Trends returned no related_topics rows")
                return empty_trends_response("No related topics data was returned.")

            return {"data": encode_trends_payload("related_topics", raw_results)}

        # Get cached result or fetch and cache
        return await cached_trends_response(cache_key, fetch_related_topics)

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error in related_topics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error") from e


# -------------------------------------------------------------------------
# 5) Trending Now
# -------------------------------------------------------------------------
@google_trends_router.get("/trending-now", summary="Trending Now")
async def trending_now(
    # === COMMONLY USED ===
    geo: str | None = Query("US", description="Location code (US, GB)", examples=["US"]),
    # === AUTH ===
    rate_limit: None = Depends(rate_limit),
):
    """Get current trending searches."""
    try:
        # Generate cache key
        cache_key = generate_cache_key("trends_trending_now", geo=geo)

        async def fetch_trending_now():
            trends_obj = await get_trends_instance()

            raw_results = await run_trends_call(
                "trending_now",
                lambda: trends_obj.trending_now(geo=geo),
            )

            if is_empty_result(raw_results):
                logger.info("Google Trends returned no trending_now rows")
                return empty_trends_response("No trending now data was returned.")

            return {"data": encode_trends_payload("trending_now", raw_results)}

        # Get cached result or fetch and cache
        return await cached_trends_response(cache_key, fetch_trending_now)

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error in trending_now: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error") from e


# -------------------------------------------------------------------------
# 6) Trending Now by RSS
# -------------------------------------------------------------------------
@google_trends_router.get("/trending-now-by-rss", summary="Trending Now (RSS)")
async def trending_now_by_rss(
    # === COMMONLY USED ===
    geo: str | None = Query("US", description="Location code (US, GB)", examples=["US"]),
    # === AUTH ===
    rate_limit: None = Depends(rate_limit),
):
    """Get trending searches with related news via RSS."""
    try:
        # Generate cache key
        cache_key = generate_cache_key("trends_trending_now_by_rss", geo=geo)

        async def fetch_trending_now_by_rss():
            trends_obj = await get_trends_instance()

            raw_results = await run_trends_call(
                "trending_now_by_rss",
                lambda: trends_obj.trending_now_by_rss(geo=geo),
            )

            if is_empty_result(raw_results):
                logger.info("Google Trends returned no trending_now_by_rss rows")
                return empty_trends_response("No trending now by RSS data was returned.")

            return {"data": encode_trends_payload("trending_now_by_rss", raw_results)}

        # Get cached result or fetch and cache
        return await cached_trends_response(cache_key, fetch_trending_now_by_rss)

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error in trending_now_by_rss: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error") from e


# -------------------------------------------------------------------------
# 7) Trending Now News by IDs
# -------------------------------------------------------------------------
@google_trends_router.get("/trending-now-news-by-ids", summary="News by IDs")
async def trending_now_news_by_ids(
    # === REQUIRED ===
    news_tokens: str = Query(
        ...,
        description=(
            "News tokens from /trending-now: comma-separated numeric IDs (4830466997,4830466998), "
            'or its news_tokens JSON array ([[4830466997,"en","US"]]). At most 50.'
        ),
    ),
    # === OPTIONS ===
    max_news: int = Query(3, ge=1, le=MAX_NEWS_ARTICLES, description="Max articles to retrieve (1-50)", examples=[3]),
    geo: str = Query(
        "US",
        pattern=r"^[A-Za-z]{2}(-[A-Za-z0-9]{1,3})?$",
        description="Location of the /trending-now call the IDs came from; applies to bare IDs only",
        examples=["US"],
    ),
    # === AUTH ===
    rate_limit: None = Depends(rate_limit),
):
    """Get related news articles for news tokens."""
    try:
        logger.debug("Received request with tokens: %s, max_news: %s", scrub(news_tokens), scrub(max_news))

        try:
            tokens = parse_news_tokens(news_tokens, geo)
        except InvalidNewsTokens as exc:
            logger.warning("Rejected news_tokens: %s", exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        logger.debug("Parsed %d news tokens", len(tokens))

        # Keyed on the normalised tokens, so "1,2" and '[[1,"en","US"],[2,"en","US"]]' share an entry.
        cache_key = generate_cache_key(
            "trends_trending_now_news_by_ids",
            news_tokens=json.dumps(tokens, separators=(",", ":")),
            max_news=max_news,
        )

        async def fetch_trending_now_news_by_ids():
            trends_obj = await get_trends_instance()

            articles = await run_trends_call(
                "trending_now_news_by_ids",
                lambda: fetch_trending_news(trends_obj, tokens, max_news),
            )

            if is_empty_result(articles):
                logger.info("Google Trends returned no trending_now_news_by_ids articles")
                return empty_trends_response("No news data was returned.")

            return {"data": encode_trends_payload("trending_now_news_by_ids", articles)}

        # Get cached result or fetch and cache
        return await cached_trends_response(cache_key, fetch_trending_now_news_by_ids)

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error in trending_now_news_by_ids: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error") from e


# -------------------------------------------------------------------------
# 8) Trending Now Showcase Timeline (Independent Historical Data)
# -------------------------------------------------------------------------
@google_trends_router.get("/trending-now-showcase-timeline", summary="Trending Timeline")
async def trending_now_showcase_timeline(
    # === REQUIRED ===
    keywords: str = Query(..., description="Comma-separated keywords", examples=["python,javascript"]),
    timeframe: HumanFriendlyBatchPeriod = Query(..., description="Time range: past_4h, past_24h, past_48h, past_7d"),
    # === AUTH ===
    rate_limit: None = Depends(rate_limit),
):
    """Get trending timeline data for keywords.

    Known limitation: Google Trends currently rejects this request (HTTP 400)
    for every keyword and timeframe, so the endpoint answers 502 with a detail
    saying a retry will not help.
    """
    # Checked 2026-09-25: the batchexecute "jpdkv" request trendspy 0.1.6
    # builds gets HTTP 400 with ["er", ..., 400, ..., 3] (INVALID_ARGUMENT),
    # including with trendspy's own defaults. Headwater passes the arguments
    # trendspy documents, so this is a Google-side contract change, and 0.1.6
    # is the latest trendspy release. HeadwaterTrends keeps the 400 visible and
    # classify_trends_failure turns it into UpstreamRejected.
    try:
        # Parse keywords
        keyword_list = [k.strip() for k in keywords.split(",") if k.strip()]
        if not keyword_list:
            logger.warning("No valid keywords provided")
            raise HTTPException(status_code=400, detail="No valid keywords provided")

        # FastAPI has already rejected anything outside the enum, and
        # BATCH_PERIOD_BY_TIMEFRAME is checked for completeness at import time,
        # so this lookup cannot miss.
        mapped_timeframe = BATCH_PERIOD_BY_TIMEFRAME[timeframe]

        # Generate cache key
        cache_key = generate_cache_key(
            "trends_trending_now_showcase_timeline", keywords=keywords, timeframe=timeframe.value
        )

        async def fetch_trending_now_showcase_timeline():
            trends_obj = await get_trends_instance()

            raw_results = await run_trends_call(
                "trending_now_showcase_timeline",
                lambda: trends_obj.trending_now_showcase_timeline(
                    keyword_list,
                    timeframe=mapped_timeframe,
                ),
            )

            if is_empty_result(raw_results):
                logger.info("Google Trends returned no showcase timeline rows")
                return empty_trends_response("No timeline data was returned.")

            return {"data": encode_trends_payload("trending_now_showcase_timeline", raw_results)}

        # Get cached result or fetch and cache
        return await cached_trends_response(cache_key, fetch_trending_now_showcase_timeline)

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error in trending_now_showcase_timeline: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error") from e


# -------------------------------------------------------------------------
# 9) Categories
# -------------------------------------------------------------------------
@google_trends_router.get("/categories", summary="Categories")
async def get_categories(
    # === SEARCH OPTIONS ===
    find: str | None = Query(None, description="Search category names", examples=["tech"]),
    root: str | None = Query(None, description="Root category ID for subcategories"),
    # === AUTH ===
    rate_limit: None = Depends(rate_limit),
):
    """Search or list Google Trends categories."""
    try:
        # Generate cache key
        cache_key = generate_cache_key("trends_categories", find=find, root=root)

        async def fetch_categories():
            trends_obj = await get_trends_instance()

            raw_results = await run_trends_call(
                "categories",
                lambda: trends_obj.categories(find=find),
            )

            if is_empty_result(raw_results):
                logger.info("Google Trends returned no categories rows")
                return empty_trends_response("No categories data was returned.")

            return {"data": encode_trends_payload("categories", raw_results)}

        # Get cached result or fetch and cache
        return await cached_trends_response(cache_key, fetch_categories, ttl=REFERENCE_DATA_TTL_SECONDS)

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error in get_categories: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error") from e


# -------------------------------------------------------------------------
# 10) Geo
# -------------------------------------------------------------------------
@google_trends_router.get("/geo", summary="Geolocations")
async def get_geo(
    # === SEARCH OPTIONS ===
    find: str | None = Query(None, description="Search location names", examples=["york"]),
    # === AUTH ===
    rate_limit: None = Depends(rate_limit),
):
    """Search available geolocation codes (countries, states, cities)."""
    try:
        # Generate cache key
        # The tree is served per language, so the key must carry it or two
        # languages collide on one entry.
        cache_key = generate_cache_key(
            "trends_geo",
            find=find,
            language=(await get_trends_instance()).language,
        )

        async def fetch_geo():
            trends_obj = await get_trends_instance()

            # Fetch and cache the whole tree per language, then filter here.
            # Caching per `find` would send an identical upstream request for
            # every distinct search term against data that never differs.
            async def fetch_all():
                rows = await run_trends_call(
                    "geo",
                    lambda: fetch_geo_locations(trends_obj, None),
                )
                return {"rows": rows}

            all_rows = (
                await cached_trends_response(
                    generate_cache_key("trends_geo_all", language=trends_obj.language),
                    fetch_all,
                    ttl=REFERENCE_DATA_TTL_SECONDS,
                )
                or {}
            ).get("rows") or []

            if find:
                needle = find.strip().lower()
                raw_results = [
                    r
                    for r in all_rows
                    if needle in (r.get("name") or "").lower() or needle in (r.get("id") or "").lower()
                ]
            else:
                raw_results = all_rows

            if is_empty_result(raw_results):
                logger.info("Google Trends returned no geo rows")
                return empty_trends_response("No geo data was returned.")

            return {"data": encode_trends_payload("geo", raw_results)}

        # Get cached result or fetch and cache
        return await cached_trends_response(cache_key, fetch_geo, ttl=REFERENCE_DATA_TTL_SECONDS)

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error in get_geo: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error") from e
