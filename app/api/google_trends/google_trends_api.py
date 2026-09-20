"""
Google Trends API Router.

Provides endpoints for Google Trends data including trending topics,
interest over time, and related queries.
"""
from fastapi import APIRouter, Query, HTTPException, Depends
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from datetime import date
import logging
import asyncio
import pandas as pd
import numpy as np
from typing import List, Optional, Union
from trendspy import Trends, BatchPeriod
from app.core.proxy import get_proxy, mask_proxy
from app.core.cache_manager import generate_cache_key, get_cached_or_fetch
from app.core.rate_limiter import rate_limit
from app.core.http_client import get_http_client_manager
from app.core.constants import USER_AGENT_LIST, REFERER_LIST
from app.schemas.enums import (
    TimeframeEnum,
    HumanFriendlyBatchPeriod,
    StandardTimeframe,
    CustomIntervalTimeframe,
)
import random
import json

# Pydantic model for date range
class DateRangeTimeframeModel(BaseModel):
    start_date: date = Field(..., description="Start date in YYYY-MM-DD format.")
    end_date: Optional[date] = Field(None, description="End date in YYYY-MM-DD format.")

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
    return df.reset_index(drop=True).to_dict(orient='records')

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
    referer = random.choice(REFERER_LIST)
    user_agent = random.choice(USER_AGENT_LIST)
    headers = {
        "Referer": referer,
        "User-Agent": user_agent,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive"
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
        rows = [r for r in rows if needle in (r["name"] or "").lower()
                or needle in (r["id"] or "").lower()]
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
        UpstreamUnavailable: if the trendspy call raised. The original
            exception is logged, never returned to the caller.
    """
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, call)
    except Exception as exc:  # noqa: BLE001 - re-raised as UpstreamUnavailable
        logger.error("Google Trends call %s failed: %s", operation, exc, exc_info=True)
        raise UpstreamUnavailable(operation) from exc


async def cached_trends_response(cache_key: str, fetch_func, ttl: Optional[int] = None):
    """Serve a Trends endpoint from cache, mapping upstream failure to 502.

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
    except Exception as exc:  # noqa: BLE001 - re-raised as UpstreamUnavailable
        logger.error(
            "Could not serialise Google Trends %s response: %s",
            operation, exc, exc_info=True,
        )
        raise UpstreamUnavailable(operation, UPSTREAM_UNUSABLE_DETAIL) from exc


def normalise_trending_news(raw):
    """Validate and normalise a ``trending_now_news_by_ids`` response.

    trendspy returns a list of rows whose third element carries the news
    payload, sometimes as a JSON string. Anything that does not fit that shape
    means the upstream contract changed.

    Raises:
        UpstreamUnavailable: on any unrecognised shape. Previously each of
            these branches answered 200 with ``{"data": []}``, which cached a
            broken upstream response for the full TTL.
    """
    operation = "trending_now_news_by_ids"

    if not isinstance(raw, list):
        logger.error("Google Trends %s returned %s, expected list", operation, type(raw))
        raise UpstreamUnavailable(operation, UPSTREAM_UNUSABLE_DETAIL)

    rows = list(raw)
    for index, row in enumerate(rows):
        if row is None or not isinstance(row, (list, tuple)) or len(row) < 3:
            logger.error("Google Trends %s row %d has unexpected shape", operation, index)
            raise UpstreamUnavailable(operation, UPSTREAM_UNUSABLE_DETAIL)

        row = list(row)
        news_data = row[2]
        if news_data is None:
            logger.error("Google Trends %s row %d carries no news payload", operation, index)
            raise UpstreamUnavailable(operation, UPSTREAM_UNUSABLE_DETAIL)

        if isinstance(news_data, str):
            try:
                row[2] = json.loads(news_data)
            except json.JSONDecodeError as exc:
                logger.error(
                    "Google Trends %s row %d has unparseable JSON: %s",
                    operation, index, exc,
                )
                raise UpstreamUnavailable(operation, UPSTREAM_UNUSABLE_DETAIL) from exc
        elif not isinstance(news_data, (dict, list)):
            logger.error(
                "Google Trends %s row %d news payload is %s",
                operation, index, type(news_data),
            )
            raise UpstreamUnavailable(operation, UPSTREAM_UNUSABLE_DETAIL)

        rows[index] = row

    return rows


def empty_trends_response(message: str) -> dict:
    """Build the 200 response used when Google Trends genuinely had no data."""
    return {"data": [], "message": message}


# -------------------------------------------------------------------------
# Helper: Create a new Trends instance per request
# -------------------------------------------------------------------------
async def get_trends_instance():
    """
    Create and return a Trends instance, applying proxy if needed and random headers.
    """
    proxy_url = await get_proxy()
    headers = get_random_headers()
    if proxy_url:
        logger.debug("TrendSpy is using proxy: %s", mask_proxy(proxy_url))
        return Trends(proxy=proxy_url, headers=headers)
    else:
        logger.debug("TrendSpy is not using any proxy.")
        return Trends(headers=headers)

# -------------------------------------------------------------------------
# 1) Interest Over Time
# -------------------------------------------------------------------------
@google_trends_router.get("/interest-over-time", summary="Interest Over Time")
async def interest_over_time(
    # === REQUIRED ===
    keywords: str = Query(..., description="Comma-separated keywords", example="python,javascript"),
    # === COMMONLY USED ===
    timeframe: str = Query("today 12-m", description="Time range: now 1-H, now 4-H, today 1-m, today 3-m, today 12-m"),
    geo: Optional[str] = Query(None, description="Location code (US, US-NY, GB)", example="US"),
    # === FILTERS ===
    cat: Optional[str] = Query(None, description="Category ID (e.g., 13=Computers)"),
    gprop: Optional[str] = Query(None, description="Property: images, youtube, news, froogle"),
    # === AUTH ===
    rate_limit: None = Depends(rate_limit)
):
    """Get search interest over time for keywords."""
    try:
        kw_list = [kw.strip() for kw in keywords.split(",") if kw.strip()]
        if not kw_list:
            raise HTTPException(status_code=400, detail="No valid keywords provided.")

        # Generate cache key
        cache_key = generate_cache_key(
            "trends_interest_over_time",
            keywords=keywords,
            timeframe=timeframe,
            geo=geo,
            cat=cat,
            gprop=gprop
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
        raise HTTPException(status_code=500, detail="Internal Server Error")

# -------------------------------------------------------------------------
# 2) Interest By Region
# -------------------------------------------------------------------------
@google_trends_router.get("/interest-by-region", summary="Interest By Region")
async def interest_by_region(
    # === REQUIRED ===
    keyword: str = Query(..., description="Single keyword", example="python"),
    # === COMMONLY USED ===
    geo: Optional[str] = Query(None, description="Location code (US, GB)", example="US"),
    resolution: str = Query("COUNTRY", description="Detail level: COUNTRY, REGION, CITY, DMA"),
    timeframe: str = Query("today 12-m", description="Time range"),
    # === FILTERS ===
    cat: Optional[str] = Query(None, description="Category ID"),
    # === AUTH ===
    rate_limit: None = Depends(rate_limit)
):
    """Get geographic breakdown of search interest."""
    try:
        # Generate cache key
        cache_key = generate_cache_key(
            "trends_interest_by_region",
            keyword=keyword,
            timeframe=timeframe,
            geo=geo,
            cat=cat,
            resolution=resolution
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
        raise HTTPException(status_code=500, detail="Internal Server Error")

# -------------------------------------------------------------------------
# 3) Related Queries (Uses a Custom Referer in the Headers)
# -------------------------------------------------------------------------
@google_trends_router.get("/related-queries", summary="Related Queries")
async def related_queries(
    # === REQUIRED ===
    keyword: str = Query(..., description="Single keyword", example="python"),
    # === COMMONLY USED ===
    geo: Optional[str] = Query(None, description="Location code (US, GB)", example="US"),
    timeframe: str = Query("today 12-m", description="Time range"),
    # === FILTERS ===
    cat: Optional[str] = Query(None, description="Category ID"),
    gprop: Optional[str] = Query(None, description="Property: images, youtube, news, froogle"),
    # === AUTH ===
    rate_limit: None = Depends(rate_limit)
):
    """Get related search queries (rising and top)."""
    try:
        # Generate cache key
        cache_key = generate_cache_key(
            "trends_related_queries",
            keyword=keyword,
            timeframe=timeframe,
            geo=geo,
            cat=cat,
            gprop=gprop
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
        raise HTTPException(status_code=500, detail="Internal Server Error")

# -------------------------------------------------------------------------
# 4) Related Topics
# -------------------------------------------------------------------------
@google_trends_router.get("/related-topics", summary="Related Topics")
async def related_topics(
    # === REQUIRED ===
    keyword: str = Query(..., description="Single keyword", example="python"),
    # === COMMONLY USED ===
    geo: Optional[str] = Query(None, description="Location code (US, GB)", example="US"),
    timeframe: str = Query("today 12-m", description="Time range"),
    # === FILTERS ===
    cat: Optional[str] = Query(None, description="Category ID"),
    gprop: Optional[str] = Query(None, description="Property: images, youtube, news, froogle"),
    # === AUTH ===
    rate_limit: None = Depends(rate_limit)
):
    """Get related topics (rising and top)."""
    try:
        # Generate cache key
        cache_key = generate_cache_key(
            "trends_related_topics",
            keyword=keyword,
            timeframe=timeframe,
            geo=geo,
            cat=cat,
            gprop=gprop
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
        raise HTTPException(status_code=500, detail="Internal Server Error")

# -------------------------------------------------------------------------
# 5) Trending Now
# -------------------------------------------------------------------------
@google_trends_router.get("/trending-now", summary="Trending Now")
async def trending_now(
    # === COMMONLY USED ===
    geo: Optional[str] = Query("US", description="Location code (US, GB)", example="US"),
    # === AUTH ===
    rate_limit: None = Depends(rate_limit)
):
    """Get current trending searches."""
    try:
        # Generate cache key
        cache_key = generate_cache_key(
            "trends_trending_now",
            geo=geo
        )

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
        raise HTTPException(status_code=500, detail="Internal Server Error")

# -------------------------------------------------------------------------
# 6) Trending Now by RSS
# -------------------------------------------------------------------------
@google_trends_router.get("/trending-now-by-rss", summary="Trending Now (RSS)")
async def trending_now_by_rss(
    # === COMMONLY USED ===
    geo: Optional[str] = Query("US", description="Location code (US, GB)", example="US"),
    # === AUTH ===
    rate_limit: None = Depends(rate_limit)
):
    """Get trending searches with related news via RSS."""
    try:
        # Generate cache key
        cache_key = generate_cache_key(
            "trends_trending_now_by_rss",
            geo=geo
        )

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
        raise HTTPException(status_code=500, detail="Internal Server Error")

# -------------------------------------------------------------------------
# 7) Trending Now News by IDs
# -------------------------------------------------------------------------
@google_trends_router.get("/trending-now-news-by-ids", summary="News by IDs")
async def trending_now_news_by_ids(
    # === REQUIRED ===
    news_tokens: str = Query(..., description="Comma-separated news tokens from trending topic"),
    # === OPTIONS ===
    max_news: int = Query(3, description="Max articles to retrieve", example=3),
    # === AUTH ===
    rate_limit: None = Depends(rate_limit)
):
    """Get related news articles for news tokens."""
    try:
        logger.debug(f"Received request with tokens: {news_tokens}, max_news: {max_news}")

        token_list = [token.strip() for token in news_tokens.split(",") if token.strip()]
        logger.debug(f"Parsed token list: {token_list}")

        if not token_list:
            logger.warning("No valid tokens found in input")
            raise HTTPException(status_code=400, detail="No valid news tokens provided.")

        # Generate cache key
        cache_key = generate_cache_key(
            "trends_trending_now_news_by_ids",
            news_tokens=news_tokens,
            max_news=max_news
        )

        async def fetch_trending_now_news_by_ids():
            trends_obj = await get_trends_instance()

            raw_results = await run_trends_call(
                "trending_now_news_by_ids",
                lambda: trends_obj.trending_now_news_by_ids(token_list, max_news=max_news),
            )

            if is_empty_result(raw_results):
                logger.info("Google Trends returned no trending_now_news_by_ids rows")
                return empty_trends_response("No news data was returned.")

            # A response in an unrecognised shape is an upstream problem, not
            # "no news": answering 200 with [] would cache the bad response.
            normalised = normalise_trending_news(raw_results)

            return {"data": encode_trends_payload("trending_now_news_by_ids", normalised)}

        # Get cached result or fetch and cache
        return await cached_trends_response(cache_key, fetch_trending_now_news_by_ids)

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error in trending_now_news_by_ids: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")

# -------------------------------------------------------------------------
# 8) Trending Now Showcase Timeline (Independent Historical Data)
# -------------------------------------------------------------------------
@google_trends_router.get("/trending-now-showcase-timeline", summary="Trending Timeline")
async def trending_now_showcase_timeline(
    # === REQUIRED ===
    keywords: str = Query(..., description="Comma-separated keywords", example="python,javascript"),
    timeframe: HumanFriendlyBatchPeriod = Query(..., description="Time range: past_4h, past_24h, past_48h, past_7d"),
    # === AUTH ===
    rate_limit: None = Depends(rate_limit)
):
    """Get trending timeline data for keywords."""
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
            "trends_trending_now_showcase_timeline",
            keywords=keywords,
            timeframe=timeframe.value
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

            return {
                "data": encode_trends_payload(
                    "trending_now_showcase_timeline", raw_results
                )
            }

        # Get cached result or fetch and cache
        return await cached_trends_response(
            cache_key, fetch_trending_now_showcase_timeline
        )

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error in trending_now_showcase_timeline: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")

# -------------------------------------------------------------------------
# 9) Categories
# -------------------------------------------------------------------------
@google_trends_router.get("/categories", summary="Categories")
async def get_categories(
    # === SEARCH OPTIONS ===
    find: Optional[str] = Query(None, description="Search category names", example="tech"),
    root: Optional[str] = Query(None, description="Root category ID for subcategories"),
    # === AUTH ===
    rate_limit: None = Depends(rate_limit)
):
    """Search or list Google Trends categories."""
    try:
        # Generate cache key
        cache_key = generate_cache_key(
            "trends_categories",
            find=find,
            root=root
        )

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
        raise HTTPException(status_code=500, detail="Internal Server Error")

# -------------------------------------------------------------------------
# 10) Geo
# -------------------------------------------------------------------------
@google_trends_router.get("/geo", summary="Geolocations")
async def get_geo(
    # === SEARCH OPTIONS ===
    find: Optional[str] = Query(None, description="Search location names", example="york"),
    # === AUTH ===
    rate_limit: None = Depends(rate_limit)
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

            all_rows = (await cached_trends_response(
                generate_cache_key("trends_geo_all", language=trends_obj.language),
                fetch_all,
                ttl=REFERENCE_DATA_TTL_SECONDS,
            ) or {}).get("rows") or []

            if find:
                needle = find.strip().lower()
                raw_results = [r for r in all_rows
                               if needle in (r.get("name") or "").lower()
                               or needle in (r.get("id") or "").lower()]
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
        raise HTTPException(status_code=500, detail="Internal Server Error")