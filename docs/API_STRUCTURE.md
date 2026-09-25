# API Structure

How the Headwater API is organised: code layout, URL scheme, auth, limits and
errors. For every endpoint and parameter, see [API_REFERENCE.md](API_REFERENCE.md).

## Directory structure

```
main.py                          # app factory, operational routes, /api/v1 router
app/
├── api/
│   ├── google_autocomplete/google_autocomplete_api.py
│   ├── google_maps/             # __init__.py builds the router from:
│   │   ├── search.py  places.py  geo.py  analytics.py
│   │   ├── jobs.py  monitors.py  health.py
│   │   ├── schemas.py  common.py
│   │   └── google_maps_api.py   # re-exports google_maps_router
│   ├── google_news/google_news_api.py
│   ├── google_trends/google_trends_api.py
│   └── youtube_transcripts/youtube_transcripts_api.py
├── core/                        # config, auth, rate limiting, cache, middleware, ...
├── schemas/                     # enums.py, responses.py
└── services/                    # upstream fetching and parsing
    └── google_maps/             # Maps scraper and service modules
```

See [ARCHITECTURE_OVERVIEW.md](ARCHITECTURE_OVERVIEW.md) for what each module does.

## Routing and versioning

`main.py` creates `APIRouter(prefix="/api/v1")` and mounts one router per service
on it, each with a `get_api_key` dependency. There is one version, `v1`.

| Prefix | Router | Operations |
|--------|--------|-----------:|
| `/api/v1/google-maps` | `google_maps_router` | 39 |
| `/api/v1/google-trends` | `google_trends_router` | 10 |
| `/api/v1/google-news` | `gnews_router` | 9 |
| `/api/v1/youtube-transcripts` | `youtube_transcripts_router` | 5 |
| `/api/v1/google-autocomplete` | `router` (autocomplete module) | 1 |
| (root) | defined in `main.py` | 6: `/health`, `/health/detailed`, `/ping`, `/status`, `/api-config`, `/config-sources` |

Paths are `/api/v1/{service}/{action}`, for example
`/api/v1/google-trends/interest-over-time`,
`/api/v1/youtube-transcripts/get-transcript`,
`/api/v1/google-maps/place/{place_id}/reviews`. Google News paths end with a slash
(`/api/v1/google-news/search/`).

## BaseRouter

`app/core/base_router.py` defines `BaseRouter`, a wrapper (not an `APIRouter`
subclass) that builds an `APIRouter` in `.router` with the API-key dependency,
a tag derived from the prefix, and default RFC 7807 error responses. The service
routers currently use plain `APIRouter` instead; `BaseRouter` is covered by
`tests/test_base_router.py`.

```python
from app.core.base_router import BaseRouter

news = BaseRouter(prefix="/google-news")  # service_name derived from the prefix


@news.get("/example")
async def example():
    return {"ok": True}

app_router = news.router  # the underlying APIRouter
```

## Authentication

Every `/api/v1/*` route, plus `/health/detailed`, `/status`, `/api-config`,
`/config-sources` and `/metrics`, needs a key from `API_KEYS` in the `X-API-Key`
header. `/health` and `/ping` are open. `ENABLE_API_KEY_AUTH=false` disables the
check.

```bash
curl "http://localhost:8000/api/v1/google-news/search/?query=ai" \
  -H "X-API-Key: your-key"
```

## Rate limiting

One limit, `RATE_LIMIT_REQUESTS` per `RATE_LIMIT_TIMEFRAME` seconds (default 100
per 3600), applied by middleware to every route. Requests with a known API key
share that key's bucket; everything else is bucketed by client IP. Responses
carry `X-RateLimit-Limit`, `X-RateLimit-Remaining` and `X-RateLimit-Reset`; a 429
adds `Retry-After`.

## Errors

Errors are RFC 7807 Problem Details (`application/problem+json`), built in
`app/core/exceptions.py`:

```json
{
  "type": "https://headwater.com/problems/not_found",
  "title": "Not Found",
  "status": 404,
  "detail": "..."
}
```

Request validation failures are FastAPI's default 422 `{"detail": [...]}`.

## Documentation endpoints

Served only when `ENVIRONMENT` is not `production`:

| Path | Content |
|------|---------|
| `/docs`, `/api/docs` | Swagger UI |
| `/redoc`, `/api/redoc` | ReDoc |
| `/openapi.json` | OpenAPI schema |
