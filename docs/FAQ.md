# Frequently Asked Questions (FAQ)

## General

### What is Headwater?

A self-hosted FastAPI service that puts Google Maps, Google News, Google Trends,
Google Autocomplete and YouTube transcripts behind one authenticated JSON API.
You run it yourself (Docker is the supported path); there is no hosted service,
sign-up, pricing tier or dashboard.

### How do I get started?

1. Follow the install steps in the [README](../README.md) or
   [DEPLOYMENT.md](DEPLOYMENT.md) (`cp .env.example .env`, then `docker compose up -d`).
2. Browse the interactive docs at `http://localhost:8000/docs` (served outside
   production only).
3. Try the requests in [EXAMPLES.md](EXAMPLES.md); parameters are in
   [API_REFERENCE.md](API_REFERENCE.md).

### Is there an SDK?

No. Use any HTTP client. The OpenAPI schema at `/openapi.json` (outside
production) can generate one.

### Do I need Google API keys?

No. Headwater reads public Google and YouTube endpoints; see
[GOOGLE_SERVICES.md](GOOGLE_SERVICES.md).

## Authentication

### How do I authenticate?

Send one of the keys configured in `API_KEYS` in the `X-API-Key` header:

```http
X-API-Key: your-key
```

There is no `Bearer` prefix. `/health` and `/ping` need no key.

### How do I add, rotate or revoke a key?

Edit `API_KEYS` (comma-separated) and restart the service. There is no key
management endpoint.

## Limits

### What are the rate limits?

Whatever you configure: `RATE_LIMIT_REQUESTS` requests per `RATE_LIMIT_TIMEFRAME`
seconds, default 100 per 3600, counted per API key (or per client IP when no known
key is sent). Every response carries `X-RateLimit-Limit`, `X-RateLimit-Remaining`
and `X-RateLimit-Reset` (seconds until the window resets). A 429 adds
`Retry-After`.

### Can Google block my server?

Yes. The upstreams throttle automated traffic by IP. Use caching
(`ENABLE_CACHE`, `CACHE_TTL`) and, if needed, rotating proxies (`ENABLE_PROXY`,
`PROXY_URLS`).

## Endpoints

### Google News: why does my search return nothing?

The search parameter is `query` (not `q`), and the paths end with a slash
(`/api/v1/google-news/search/`). Beyond that, a narrow query, `exact_match=true`
or a tight `start_date`/`end_date` range can legitimately return no articles.

### Google Trends: interest-over-time vs interest-by-region?

- `/google-trends/interest-over-time` takes comma-separated `keywords` and returns
  interest over a `timeframe`.
- `/google-trends/interest-by-region` takes one `keyword` and returns interest per
  region at a `resolution` (default `COUNTRY`).

Both can return `{"data": [], "message": ...}` when Google has no data, for
example for low-volume terms.

### Google Autocomplete: what does `variations=true` do?

Instead of the raw suggestions for `q`, it expands the query into many related
queries, fetches them in parallel (up to `AUTOCOMPLETE_MAX_PARALLEL_REQUESTS`) and
returns the result under `keyword_data`.

### YouTube: why is there no transcript for a video?

Common reasons: the owner disabled captions, there is no transcript in the
languages you asked for (check `/youtube-transcripts/list-transcripts` first), or
YouTube is refusing requests from your server's IP.

### Does Headwater send webhooks?

Yes, for Google Maps monitors. `POST /api/v1/google-maps/monitors` watches a
place (`place_id` or `url`) every `check_interval_hours` and can take a
`webhook_url` for change notifications; `POST /api/v1/google-maps/webhooks`
registers a receiver for a list of `events`. Webhook targets must resolve to public addresses, and
`MAPS_WEBHOOK_ALLOWED_HOSTS` can restrict them further.

## Troubleshooting

| Status | Meaning |
|--------|---------|
| 401 | Missing or unknown `X-API-Key` |
| 422 | Invalid or missing parameters |
| 429 | Rate limit reached, Headwater's own (`rate_limit_exceeded`) or Google's (`upstream_rate_limited`); wait `Retry-After` seconds |
| 500 "no API keys are configured" | Auth is on but `API_KEYS` is empty |
| 502 / 503 | An upstream failed or blocked the request, or the rate limiter backend (Redis) is unreachable |

Errors other than 422 are `application/problem+json` bodies with `type`, `title`,
`status` and `detail`. See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for more.

## Data and legal

### Does Headwater store my data?

There is no database. Responses are cached for `CACHE_TTL` seconds (Redis when
`REDIS_URL` is set, memory otherwise). Google Maps jobs, monitors and webhooks are
kept in Redis, or in memory without it.

### Can I use it commercially?

The code is MIT licensed (see `LICENSE`). You are responsible for complying with
the terms of Google, YouTube and any site you fetch through it.

### How do I report bugs or request features?

Open an issue at https://github.com/HouseofLoops/headwater/issues. For security
problems, see [SECURITY_GUIDELINES.md](SECURITY_GUIDELINES.md#reporting-a-vulnerability).
