# Architecture Overview

Headwater is one FastAPI application (`main.py`) that exposes five Google/YouTube
data sources under `/api/v1`. It has no database. Redis is optional: when
`REDIS_URL` is set it backs rate limiting, caching and Maps records; otherwise
each of those falls back to process memory.

```
Client ──HTTP──▶ FastAPI app (main.py, uvicorn :8000)
                  │  middleware: rate limit, request log, security headers, gzip, (trusted host), CORS
                  │  route dependency: X-API-Key check
                  ▼
                app/api/<service>/  (routers)
                  ▼
                app/services/       (fetching and parsing)
                  │                         │
                  ▼                         ▼
                Redis (optional)          Google / YouTube (httpx, gnews, trendspy,
                or process memory         youtube-transcript-api, Playwright Chromium)
```

## Code layout

| Path | Contents |
|------|----------|
| `main.py` | App factory (`create_application`), lifespan (startup/shutdown), `/health`, `/health/detailed`, `/ping`, `/status`, `/api-config`, `/config-sources`, docs routes, `/metrics`, and the `/api/v1` router |
| `app/api/google_autocomplete/` | `google_autocomplete_api.py` |
| `app/api/google_maps/` | Routers split by area: `search.py`, `places.py`, `geo.py`, `analytics.py`, `jobs.py`, `monitors.py`, `health.py`; request models in `schemas.py`; shared helpers in `common.py`; assembled in `__init__.py` (`google_maps_api.py` re-exports the router for `main.py`) |
| `app/api/google_news/` | `google_news_api.py` |
| `app/api/google_trends/` | `google_trends_api.py` (calls `trendspy` directly) |
| `app/api/youtube_transcripts/` | `youtube_transcripts_api.py` |
| `app/services/` | `google_autocomplete_service.py`, `google_news_service.py`, `google_news_article_service.py`, `google_news_catalog.py`, `youtube_transcripts_service.py`, `google_maps_service.py`, `google_maps_scraper.py`, `google_maps_monitors.py`, `record_store.py` |
| `app/services/google_maps/` | Maps implementation modules: `scraper.py`, `scraper_place_details.py`, `scraper_jobs.py`, `scraper_limits.py`, `scraper_errors.py`, `service_area_search.py`, `service_directions.py`, `service_menu.py`, `service_monitors.py`, `service_place_content.py`, `service_reservations.py`, `constants.py` |
| `app/schemas/` | `enums.py`, `responses.py` |
| `app/core/` | Shared infrastructure (below) |

### `app/core`

| Module | Role |
|--------|------|
| `config.py` | `Settings` (pydantic-settings), `get_settings()`, placeholder-credential check |
| `auth.py` | `X-API-Key` validation (`get_api_key`) |
| `rate_limiter.py` | `RateLimitMiddleware`, Redis or in-memory counters, fail-closed policy |
| `middleware.py` | CORS policy, trusted hosts, gzip, security headers, request logging (`X-Request-ID`) |
| `exceptions.py` | Exception classes and RFC 7807 handlers |
| `cache_manager.py`, `cache_backends.py` | Response cache (`get_cached_or_fetch`); Redis, memory and tiered backends |
| `redis_manager.py` | Shared async Redis connection |
| `http_client.py` | Pooled `httpx` client manager |
| `proxy.py` | Outbound proxy selection (`PROXY_URLS`, `NO_PROXY_HOSTS`) |
| `url_guard.py` | SSRF checks for caller-supplied URLs |
| `identity.py` | Keyed digests (`SECRET_KEY`) used as storage keys |
| `input_sanitizer.py` | Query length and pattern checks |
| `log_safety.py` | Log-injection filter and `scrub()` |
| `health_checks.py` | Redis, upstream and system checks for `/health/detailed` |
| `dependencies.py` | FastAPI dependency helpers |
| `base_router.py` | `BaseRouter` wrapper (API-key dependency and default error responses); the current routers use plain `APIRouter` |
| `search.py` | Search structures for autocomplete suggestions |
| `constants.py` | Shared constants |
| `text_utils.py`, `datetime_utils.py`, `serialization_utils.py`, `introspection_utils.py`, `collection_utils.py`, `url_utils.py`, `decorators.py`, `file_utils.py` | Helpers; `utils.py` only re-exports them |

## Request flow

1. `RateLimitMiddleware` (outermost) counts the request against the caller's API
   key or client IP and returns 429 or 503 before anything else runs.
2. Request logging, security headers, gzip, trusted-host (production only) and
   CORS middleware run.
3. The `/api/v1` routers carry a `get_api_key` dependency, which returns 401
   for a missing or unknown key.
4. FastAPI validates parameters (422 on failure).
5. The endpoint calls its service. Cacheable responses go through
   `get_cached_or_fetch` (used by every service's API module), so a repeat
   request within the TTL skips the upstream call.
6. Errors are turned into `application/problem+json` by the handlers in
   `app/core/exceptions.py`:

```json
{
  "type": "https://headwater.com/problems/not_found",
  "title": "Not Found",
  "status": 404,
  "detail": "..."
}
```

## Background work

Started in the lifespan hook in `main.py`:

- The rate limiter's cleanup task for the in-memory store.
- The Maps monitor scheduler (`start_monitor_scheduler`), which re-scrapes
  watched places on their interval and calls webhooks on change.
- A startup check that logs whether Maps records are durable (Redis) or
  memory-only.

Maps searches can run as background jobs (`wait_for_results=false`); jobs,
monitors and webhooks are stored through `RecordStore` and scoped to the API key
that created them.

## Deployment shape

One container (`Dockerfile`: `python:3.14-slim-trixie`, non-root, Playwright
Chromium, `uvicorn --workers 1`) plus Redis, as in `docker-compose.yml`. Running
several workers or replicas requires Redis so that rate-limit counters and Maps
records are shared. See [DEPLOYMENT.md](DEPLOYMENT.md).

## Security

Summarised in [SECURITY_GUIDELINES.md](SECURITY_GUIDELINES.md): API-key auth,
per-key/per-IP rate limiting, CORS and trusted-host rules in production, security
headers, SSRF allow-lists for caller-supplied URLs, and owner-scoped Maps records.
