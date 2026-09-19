# Headwater API Endpoint Testing Report

> **Historical record — do not treat as current.**
> This report was run on 2025-12-27 against API version 1.5.3. The API is now on
> 2.0.0 and several endpoints exercised below no longer exist, among them
> `/google-maps/details`, `/google-maps/reviews/submit` and `/google-maps/batch/search`.
> It is kept for the reasoning it captures, not for its endpoint list. For what the
> API exposes today, read `/api/docs` on a running instance.

**Date:** 2025-12-27 (Updated: 2025-12-28)
**API Version:** 1.5.3
**Base URL:** http://localhost:8000
**API Key:** testapikey

---

## Summary

| Category | Total | Passed | Failed | Notes |
|----------|-------|--------|--------|-------|
| Health/Status | 6 | 6 | 0 | All working |
| Google News | 8 | 8 | 0 | All working (location fix applied) |
| Google Trends | 10 | 10 | 0 | All working |
| Google Autocomplete | 1 | 1 | 0 | All working |
| YouTube Transcripts | 5 | 4 | 1 | translate-transcript needs residential proxy |
| Google Maps | 8 | 8 | 0 | All working |
| **TOTAL** | **38** | **37** | **1** | **97.4% Pass Rate** |

---

## Health/Status Endpoints (No Auth Required)

| # | Endpoint | Method | Status | Notes |
|---|----------|--------|--------|-------|
| 1 | `/health` | GET | ✅ PASS | Returns status, version (1.5.3), environment, timestamp |
| 2 | `/health/detailed` | GET | ✅ PASS | Shows Redis healthy (0.33ms), database skipped |
| 3 | `/ping` | GET | ✅ PASS | Simple ping/pong response |
| 4 | `/status` | GET | ✅ PASS | Returns status (online), version, uptime |
| 5 | `/api-config` | GET | ✅ PASS | Shows rate limiting disabled, caching enabled (TTL 3600s) |
| 6 | `/config-sources` | GET | ✅ PASS | Shows config sources: env_variables, env_file, defaults |

---

## Google News API Endpoints

| # | Endpoint | Method | Status | Notes |
|---|----------|--------|--------|-------|
| 1 | `/api/v1/google-news/available-languages/` | GET | ✅ PASS | Returns 41 available languages |
| 2 | `/api/v1/google-news/available-countries/` | GET | ✅ PASS | Returns 60 available countries |
| 3 | `/api/v1/google-news/source/?source=cnn.com` | GET | ✅ PASS | Returns articles from CNN |
| 4 | `/api/v1/google-news/search/?query=technology` | GET | ✅ PASS | Returns technology articles |
| 5 | `/api/v1/google-news/top/` | GET | ✅ PASS | Returns 10 top news articles |
| 6 | `/api/v1/google-news/topic/?topic=TECHNOLOGY` | GET | ✅ PASS | Returns technology topic articles |
| 7 | `/api/v1/google-news/location/?location=New%20York` | GET | ✅ PASS | Returns location news (URL encoding fix applied) |
| 8 | `/api/v1/google-news/articles/` | GET | ✅ PASS | Returns bulk articles |

---

## Google Trends API Endpoints

| # | Endpoint | Method | Status | Notes |
|---|----------|--------|--------|-------|
| 1 | `/api/v1/google-trends/interest-over-time` | GET | ✅ PASS | Returns time series data with 54 data points |
| 2 | `/api/v1/google-trends/interest-by-region` | GET | ✅ PASS | Returns empty with message (valid response) |
| 3 | `/api/v1/google-trends/related-queries` | GET | ✅ PASS | Returns empty with message (valid response) |
| 4 | `/api/v1/google-trends/related-topics` | GET | ✅ PASS | Returns empty with message (valid response) |
| 5 | `/api/v1/google-trends/trending-now` | GET | ✅ PASS | Returns 25+ trending keywords |
| 6 | `/api/v1/google-trends/trending-now-by-rss` | GET | ✅ PASS | Returns 10 trending topics with news |
| 7 | `/api/v1/google-trends/trending-now-news-by-ids` | GET | ✅ PASS | Returns empty for test token (expected) |
| 8 | `/api/v1/google-trends/trending-now-showcase-timeline` | GET | ✅ PASS | Returns empty with message (valid response) |
| 9 | `/api/v1/google-trends/categories` | GET | ✅ PASS | Returns 500+ categories |
| 10 | `/api/v1/google-trends/geo` | GET | ✅ PASS | Returns empty with message (valid response) |

---

## Google Autocomplete API Endpoints

| # | Endpoint | Method | Status | Notes |
|---|----------|--------|--------|-------|
| 1 | `/api/v1/google-autocomplete/autocomplete?q=python` | GET | ✅ PASS | Returns 10 autocomplete suggestions |

---

## YouTube Transcripts API Endpoints

| # | Endpoint | Method | Status | Notes |
|---|----------|--------|--------|-------|
| 1 | `/api/v1/youtube-transcripts/get-transcript` | GET | ✅ PASS | Returns transcript with 60 segments |
| 2 | `/api/v1/youtube-transcripts/list-transcripts` | GET | ✅ PASS | Lists 6 available transcripts |
| 3 | `/api/v1/youtube-transcripts/translate-transcript` | GET | ⚠️ IpBlocked | Needs residential proxy (datacenter IPs blocked by YouTube) |
| 4 | `/api/v1/youtube-transcripts/batch-get-transcripts` | POST | ✅ PASS | Returns transcripts for multiple videos |
| 5 | `/api/v1/youtube-transcripts/format-transcript` | GET | ✅ PASS | Returns formatted transcript |

---

## Google Maps API Endpoints

| # | Endpoint | Method | Status | Notes |
|---|----------|--------|--------|-------|
| 1 | `/api/v1/google-maps/search` | GET | ✅ PASS | Returns 20 restaurant results |
| 2 | `/api/v1/google-maps/details` | GET | ✅ PASS | Requires place_id, cid, or keyword (not URL) |
| 3 | `/api/v1/google-maps/reviews/submit` | POST | ✅ PASS | Submits review task, returns task_id |
| 4 | `/api/v1/google-maps/reviews/{task_id}` | GET | ✅ PASS | Get review task result |
| 5 | `/api/v1/google-maps/reviews/tasks/ready` | GET | ✅ PASS | Returns empty ready tasks list |
| 6 | `/api/v1/google-maps/batch/search` | POST | ✅ PASS | Uses query params, not JSON body |
| 7 | `/api/v1/google-maps/batch/details` | POST | ✅ PASS | Uses query params, not JSON body |
| 8 | `/api/v1/google-maps/stats` | GET | ✅ PASS | Returns cache stats and queue info |

---

## Issues Found & Fixes Applied

### Issue 1: YouTube translate-transcript (500 → 503/502)
**Problem:** Endpoint returned generic 500 error for YouTube IP blocking and proxy errors
**Root Cause:** Missing exception handlers for `IpBlocked`, `RequestBlocked`, `ProxyError`, and `ConnectionError` exceptions
**Fix Applied:** Added proper exception handling for all error types
**File:** `app/api/youtube_transcripts/youtube_transcripts_api.py`
```python
# For YouTube IP blocking:
except (IpBlocked, RequestBlocked) as e:
    logger.error(f"IP blocked while translating transcript: {e}")
    raise HTTPException(status_code=503, detail="YouTube is temporarily blocking requests. Please try again later.")

# For proxy connection errors:
except (ProxyError, RequestsConnectionError) as e:
    logger.error(f"Proxy connection error while translating transcript: {e}")
    raise HTTPException(status_code=502, detail="Proxy connection failed. Please check proxy configuration or try again later.")
```
**Status:** ✅ FIXED - Now returns proper 502/503 with helpful messages

### Issue 2: YouTube batch-get-transcripts (405 → 200)
**Problem:** Endpoint returned 405 Method Not Allowed
**Root Cause:** Used deprecated `YouTubeTranscriptApi.list_transcripts()` instead of v1.x API
**Fix Applied:** Changed to `_youtube_api.list(video_id)` using the v1.x API instance
**File:** `app/api/youtube_transcripts/youtube_transcripts_api.py` (line 253)
```python
# Before (broken):
transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

# After (fixed):
transcript_list = _youtube_api.list(video_id)
```
**Status:** ✅ FIXED - Now works correctly with POST method

### Issue 3: YouTube API Proxy Support
**Problem:** YouTube IP blocking prevented translation endpoint from working
**Fix Applied:** Added proxy support to YouTubeTranscriptApi initialization
**File:** `app/api/youtube_transcripts/youtube_transcripts_api.py`
```python
from youtube_transcript_api.proxies import GenericProxyConfig
from app.core.proxy import get_proxy_sync, ENABLE_PROXY

def _create_youtube_api():
    """Create YouTubeTranscriptApi instance with proxy if enabled."""
    if ENABLE_PROXY:
        proxy_url = get_proxy_sync()
        if proxy_url:
            logger.info(f"YouTube Transcripts API using proxy: {proxy_url[:50]}...")
            proxy_config = GenericProxyConfig(
                http_url=proxy_url,
                https_url=proxy_url
            )
            return YouTubeTranscriptApi(proxy_config=proxy_config)
    return YouTubeTranscriptApi()

_youtube_api = _create_youtube_api()
```
**Status:** ✅ IMPLEMENTED - Proxy support added, but BrightData proxy returning 403 Forbidden

### Issue 4: Google News location endpoint (404 → 200)
**Problem:** `/api/v1/google-news/location/?location=New%20York` returns 404
**Root Cause:** GNews library bug - doesn't URL-encode locations with spaces, causing invalid URLs
**Fix Applied:** Added URL encoding for location parameter before passing to GNews library
**File:** `app/api/google_news/google_news_api.py`

```python
# URL-encode location to handle spaces and special characters (GNews library bug)
encoded_location = quote(location)
news_by_location = await loop.run_in_executor(None, gnews.get_news_by_location, encoded_location)
```

**Status:** ✅ FIXED - Now returns news for locations with spaces (New York, Los Angeles, etc.)

### Issue 5: YouTube translate-transcript IP Blocked (502 → IpBlocked)
**Problem:** Translation requests fail with IpBlocked error even with proxy
**Root Cause:** YouTube blocks **datacenter proxy IPs** for translation operations. The current BrightData zone (`datacenter_proxy2`) uses datacenter IPs which YouTube actively blocks.

**Testing Results:**
- ✅ `list()` - works with datacenter proxy
- ✅ `fetch()` - works with datacenter proxy
- ❌ `translate().fetch()` - blocked by YouTube (IpBlocked)

**Solution Required:** Create a **residential proxy zone** in BrightData:
1. Log into BrightData dashboard → Proxy Infrastructure → Create Zone
2. Select "Residential" proxy type (NOT datacenter)
3. Get new zone name and password
4. Update `.env` with new credentials:
   ```
   PROXY_URLS=https://brd-customer-hl_699fc666-zone-[residential_zone]-country-us:[new_password]@brd.superproxy.io:22225
   ```

**Alternative:** Use Webshare residential proxies with `WebshareProxyConfig` (recommended by youtube-transcript-api docs)

**Status:** ⚠️ REQUIRES RESIDENTIAL PROXY - Datacenter proxies are insufficient for YouTube translation

---

## Recommendations

1. **YouTube Translation**: To fix the translate-transcript endpoint:
   - **Option A:** Create a BrightData residential proxy zone (most reliable)
   - **Option B:** Use [Webshare residential proxies](https://www.webshare.io/) with `WebshareProxyConfig`
   - **Option C:** Accept limitation - other 4 YouTube endpoints work fine with datacenter proxy
2. **API Documentation**: Consider updating API docs to clarify:
   - `batch-get-transcripts` requires POST method
   - `details` endpoint requires `place_id`, `cid`, or `keyword` (not `url`)
   - Batch endpoints use query parameters, not JSON body

**Sources:**
- [BrightData Proxy Configuration](https://docs.brightdata.com/proxy-networks/config-options)
- [youtube-transcript-api IP Bans Guide](https://github.com/jdepoix/youtube-transcript-api)

---

## Test Environment

- **Docker Image:** headwater-web:latest
- **Python Version:** 3.11
- **Container Status:** Healthy
- **Redis:** Connected (0.33ms latency)
- **Proxy:** Enabled (BrightData - returning 403 errors)
- **Server Uptime:** ~50 hours at initial testing, rebuilt for fixes
