# Troubleshooting Guide

This guide helps you diagnose and resolve common issues with the Headwater API.

Headwater has no database and needs no Google API key. Its only external
dependency is Redis, which is optional but recommended. Every setting named here
is a field on `Settings` in `app/core/config.py` unless it is marked as read
directly from the environment. Settings are loaded once per process, so restart
the app after changing any of them.

## Table of Contents

- [First checks](#first-checks)
- [Startup failures](#startup-failures)
- [Status codes and what they mean](#status-codes-and-what-they-mean)
- [Authentication](#authentication)
- [Rate limiting](#rate-limiting)
- [Redis and durability](#redis-and-durability)
- [Proxies and blocked IPs](#proxies-and-blocked-ips)
- [Service-specific issues](#service-specific-issues)
- [Logs and metrics](#logs-and-metrics)
- [Getting help](#getting-help)

## First checks

```bash
# Liveness. Unauthenticated; returns {"status": "healthy"} and nothing else.
curl http://localhost:8000/health

# Dependency checks plus record durability. Requires a key.
curl -H "X-API-Key: $KEY" http://localhost:8000/health/detailed

# Version, environment and uptime. Requires a key.
curl -H "X-API-Key: $KEY" http://localhost:8000/status

# Effective rate-limit, cache and CORS configuration. Requires a key.
curl -H "X-API-Key: $KEY" http://localhost:8000/api-config

# Under Docker Compose
docker compose ps
docker compose logs -f web
docker compose exec redis redis-cli -a "$REDIS_PASSWORD" ping
```

`/health/detailed` returns HTTP 200 whatever it finds. Read the body:

| Field | Meaning |
|-------|---------|
| `status` | `healthy`, `warning`, `degraded` or `unhealthy`, the worst of the checks |
| `checks.redis` | `skipped` when `REDIS_URL` is unset, otherwise `healthy` with a ping time |
| `checks.system` | CPU, memory and disk percentages; each shows `warning` at 90% or above |
| `checks.external_apis` | No upstreams are probed at present, so this is always `healthy` with an empty `apis` map |
| `record_storage_durable` | `true` only when Maps jobs, monitors and webhooks are stored in Redis. See [Redis and durability](#redis-and-durability) |

`/docs`, `/redoc`, `/openapi.json`, `/api/docs` and `/api/redoc` exist only
when `ENVIRONMENT` is not `production`. In production they return 404 by design.

## Startup failures

| Message in the log | Cause | Fix |
|--------------------|-------|-----|
| `SettingsError: Invalid application configuration; ... REDIS_URL (url_parsing)` | `REDIS_URL` is set but empty or malformed (`REDIS_URL=` counts) | Comment the line out, or give a full `redis://` URL. The error never prints values, only field names |
| `API_KEYS, SECRET_KEY still holds the placeholder value shipped in .env.example` | `ENVIRONMENT` is not one of `development`, `dev`, `local`, `test`, `testing`, and a credential is still the `.env.example` placeholder | Generate real values for `API_KEYS`/`API_KEY` and `SECRET_KEY` |
| `CORSConfigurationError: CORS_ORIGINS must list explicit origins in production` | `ENVIRONMENT=production` with `CORS_ORIGINS` left at its default `*` | Set `CORS_ORIGINS` to a comma-separated allow-list, e.g. `https://app.example.com` |
| `RateLimiterConfigurationError: Rate limiting is enabled with N worker processes but no REDIS_URL is configured` | `ENVIRONMENT` is `production`, `prod` or `staging`, more than one worker, `RATE_LIMIT_ENABLED=true`, no Redis | Set `REDIS_URL`, or run one worker |

Outside production a wildcard `CORS_ORIGINS` only logs a warning, and CORS
credentials are disabled while the wildcard is in place.

The worker count is taken from `--workers`/`-w` on the command line and from
`WEB_CONCURRENCY`, `UVICORN_WORKERS`, `GUNICORN_WORKERS` or `WORKERS`. It cannot
see several containers behind a load balancer; those need `REDIS_URL` too.

### Container starts but is unhealthy

- The image runs `uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1` and
  health-checks `curl -f http://localhost:8000/health` every 30 seconds.
- `docker-compose.yml` sets `REDIS_URL` for the `web` service itself, pointing at
  the `redis` service. That hostname only resolves inside the compose network, so
  do not copy it into a `.env` used for a bare `uvicorn` run.
- `/health` is rate limited like every other path (see below). The health check
  arrives from `localhost` with no key, so it has its own per-IP bucket. With the
  defaults (100 requests per 3600 seconds) the check alone makes 120 requests an
  hour, so `/health` starts answering 429 about 50 minutes into each window and
  the container is reported unhealthy until the window resets. Raise
  `RATE_LIMIT_REQUESTS`, lower `RATE_LIMIT_TIMEFRAME`, or lengthen the health
  check interval.

## Status codes and what they mean

| Code | Where | Meaning |
|------|-------|---------|
| 400 `Invalid host header` | Any path, `ENVIRONMENT=production` only | See [Host header rejected](#host-header-rejected-in-production) |
| 400 | Maps search | `max_results` cannot finish within `timeout`; the message names the largest `max_results` that fits |
| 400 | News `article-details` | The URL's host is not on the allow-list |
| 401 | Any authenticated path | Missing or unknown `X-API-Key` |
| 403 | YouTube | Transcripts are disabled for that video |
| 404 | News, YouTube, Maps jobs | No results, no transcript, or a job/monitor/webhook this key cannot see |
| 422 | Any | Request validation failed; the body lists the fields |
| 429 | Any | Headwater's rate limit, or an upstream 429 passed through by Autocomplete (see below) |
| 500 `API key authentication is enabled but no API keys are configured.` | Any authenticated path | `ENABLE_API_KEY_AUTH=true` with empty `API_KEYS` and `API_KEY` |
| 502 | Trends, News `article-details`, YouTube, Autocomplete | The upstream call failed; for YouTube, `Proxy connection failed` |
| 503 `Rate limiting is temporarily unavailable` | Any | The rate limiter could not reach its store (see [Rate limiting](#rate-limiting)) |
| 503 | YouTube | YouTube is blocking the outbound IP |
| 503 | Maps | `Google Maps scraper service is unavailable`: the scraper health check failed, e.g. Playwright is not installed |
| 504 | YouTube | YouTube did not respond within the HTTP timeouts |

## Authentication

Send the key in the `X-API-Key` header. There is no bearer-token or query-string
alternative.

- `API_KEYS` takes `key1,key2` or `["key1","key2"]`. `API_KEY` (single key) is
  merged into the same set.
- `ENABLE_API_KEY_AUTH=false` makes every route public, including `/metrics`,
  `/status`, `/api-config` and `/health/detailed`.
- `/health` and `/ping` never need a key.
- `401 Missing API key. Provide it in the X-API-Key header.` means the header
  did not arrive. Check that a reverse proxy is not stripping it.
- `401 Invalid API key provided.` means the value is not in the configured set.
  Look for stray quotes or whitespace in `.env`.

## Rate limiting

A fixed window of `RATE_LIMIT_REQUESTS` (default 100) per
`RATE_LIMIT_TIMEFRAME` seconds (default 3600). Switch it off with
`RATE_LIMIT_ENABLED=false`.

- **Buckets.** A request carrying a known API key is counted against that key.
  Anything else (no key, or an unknown key) is counted against the client IP.
- **Responses.** A limited request gets 429 with a `Retry-After` header and a
  JSON body with `limit` and `reset`. Allowed responses carry
  `X-RateLimit-Limit`, `X-RateLimit-Remaining` and `X-RateLimit-Reset`.
- **Every path counts,** including `/health`, `/ping` and `/metrics`, and the
  limiter runs before authentication, so a client with an exhausted IP bucket
  gets 429 rather than 401.
- **`/api/v1` requests count twice.** Those routes are checked by both the
  middleware and a per-route dependency, against the same bucket, so each call
  uses two units. The effective budget for API calls is half of
  `RATE_LIMIT_REQUESTS`, and the `X-RateLimit-Remaining` value on the response
  is one higher than what is left.
- **Behind a reverse proxy.** `X-Forwarded-For` is ignored. Without it, every
  keyless client shares the proxy's IP bucket. Terminate the header in the proxy
  and run uvicorn with `--proxy-headers --forwarded-allow-ips=<proxy IP>`.
- **Several workers, no Redis.** Outside production-like environments this runs,
  but each worker keeps its own counters, so the real limit is multiplied by the
  worker count. A warning is logged.
- **503 on every request.** `REDIS_URL` is set but Redis is unreachable. The
  limiter fails closed. Fix Redis, or set `RATE_LIMIT_FAIL_OPEN=true` (read
  directly from the environment) to let requests through uncounted; each one is
  then logged at ERROR.

## Redis and durability

Redis is used for three things. Without it each falls back differently:

| Use | With Redis | Without Redis |
|-----|-----------|---------------|
| Response cache | Shared by all workers | Per-process memory; no size cap, expired entries are dropped only when read |
| Rate limit counters | Shared | Per-process (refused in production with several workers) |
| Maps jobs, monitors, webhooks | Survive restarts, visible to every worker (7-day expiry) | Memory only; lost on restart and invisible to sibling workers |

At startup the log says either `Record storage is durable (Redis)` or
`Record storage is NOT durable`. `record_storage_durable` in `/health/detailed`
reports the same thing, or `"unknown"` if the check itself failed. If it is
`false` while you expect Redis:

1. Confirm `REDIS_URL` is set in the environment the app actually sees (`docker
   compose exec web env`).
2. Include the password: `redis://:<password>@<host>:6379`. In Compose the
   password comes from `REDIS_PASSWORD`, which Settings does not read.
3. Check `checks.redis` in `/health/detailed` for the connection result.

The Redis client uses a 5 second connect and socket timeout; these are fixed in
`app/core/redis_manager.py`.

A `404 Job not found` for a job you just created usually means the job was
written to memory on a different worker, the app restarted without Redis, or the
request used a different API key. Jobs, monitors and webhooks are only visible to
the key that created them.

## Proxies and blocked IPs

| Setting | Default | Notes |
|---------|---------|-------|
| `ENABLE_PROXY` | `false` | Nothing is proxied unless this is `true` |
| `PROXY_URLS` | unset | Comma-separated. Malformed entries are skipped with a warning |
| `PROXY_URL` | unset | Single proxy, merged with `PROXY_URLS` |
| `NO_PROXY_HOSTS` | unset | Comma-separated domain suffixes that must go direct |

- `Proxying is enabled but no valid proxy URLs are configured` in the log means
  `ENABLE_PROXY=true` with nothing usable in `PROXY_URLS`/`PROXY_URL`. Requests
  go out direct.
- Autocomplete, Trends and News rotate through the proxies round-robin and
  ignore `NO_PROXY_HOSTS`.
- Maps and YouTube use the first proxy and honour `NO_PROXY_HOSTS`. Google Maps
  browser navigation through a datacentre proxy tends not to complete; if Maps
  requests hang only when proxying is on, add `google.com` to `NO_PROXY_HOSTS`.
  That also bypasses the proxy for any other Maps request to `www.google.com`.
- YouTube `503 YouTube is temporarily blocking requests` is YouTube refusing the
  outbound IP (`IpBlocked`/`RequestBlocked`). Datacentre and cloud IPs are the
  usual cause. Route YouTube through a residential proxy, and if the provider
  refuses YouTube tunnels, add `youtube.com` to `NO_PROXY_HOSTS` instead.
- YouTube `502 Proxy connection failed` means the proxy itself refused or
  dropped the connection. Proxy credentials are masked in the logs.

## Service-specific issues

### Google Maps

- **Slow.** A search opens each place in turn, at about 12 seconds per result.
  The default `max_results` is 20 and the ceiling is 45; `timeout` defaults to
  300 and can be 30 to 600 seconds. A size and timeout that cannot finish are
  rejected up front with 400 and a suggested `max_results`.
- For large searches, use `wait_for_results=false`. You get a `job_id` back at
  once; poll `GET /api/v1/google-maps/jobs/{job_id}` and fetch
  `/jobs/{job_id}/results`.
- **Grid and bulk searches return fewer points than requested.** Fan-out is
  capped at `GOOGLE_MAPS_MAX_FANOUT` (default 25, read directly from the
  environment). The truncation is logged.
- **Requests queue.** Each worker runs at most `GOOGLE_MAPS_MAX_CONCURRENT_BROWSERS`
  Chromium instances at once (default 4, read directly from the environment).
- **Out-of-memory kills.** Each Chromium is roughly 100 MB. The Compose file
  gives the `web` container a 2 GB limit; keep workers x browsers within it.
- `GET /api/v1/google-maps/health` reports whether the scraper can start.
- A scrape failure, including Google changing its page markup so nothing can be
  parsed, marks the job failed with the real error. It is never reported as a
  completed job with an empty list.

### Google Autocomplete

- A 429 or other non-200 whose detail is `Failed to retrieve suggestions` came
  from Google and was passed through. Headwater's own 429 has the title
  `Too Many Requests` and a `Retry-After` header.
- With `INPUT_SANITIZATION_ENABLED=true` (default) the query is cleaned rather
  than rejected: it is truncated to `MAX_QUERY_LENGTH` (default 200) characters,
  and characters outside `ALLOWED_CHARACTERS_PATTERN` are removed. The default
  pattern is ASCII only, so accented and non-Latin queries lose characters.
  Widen the pattern or disable sanitization if that matters to you.
- `variations=true` fans out up to `AUTOCOMPLETE_MAX_PARALLEL_REQUESTS`
  (default 10) requests at once.

### Google News

- **`summary` and `keywords` are `null`, with `nlp_available: false`.** nltk is
  deliberately not installed (see the comment in
  `app/services/google_news_article_service.py`). Everything else in the article
  response still works. If nltk is installed but its corpus is missing, the
  response also carries an `error` field and is not cached.
- **`article-details` returns 400.** Only Google News hosts are allowed by
  default. Add publisher hosts with `NEWS_ARTICLE_ALLOWED_HOSTS` (comma-separated;
  a leading dot matches subdomains, e.g. `.bbc.co.uk`). Plain `http://` URLs are
  refused unless `NEWS_ARTICLE_ALLOW_HTTP=true`. Both are read directly from the
  environment.
- **502 `Could not retrieve the requested article.`** The publisher fetch failed.
  The real reason is in the log.
- **404** means the feed had no items for those parameters.

### Google Trends

- 502 means the call to Google Trends failed. Failures are not cached, so the
  next request tries again. Google Trends throttles aggressively; proxies help.

### YouTube transcripts

- 404: no transcript for that video, or the video is unavailable.
- 403: transcripts are disabled for the video.
- 504: YouTube did not answer within `HTTP_CONNECTION_TIMEOUT` (default 10.0)
  and `HTTP_READ_TIMEOUT` (default 30.0) seconds. Raise them if your network or
  proxy is slow.

### Host header rejected in production

With `ENVIRONMENT=production`, `TrustedHostMiddleware` accepts only the hosts
`api.headwater.com`, `headwater.com` and `localhost`. Any other `Host` header
gets `400 Invalid host header`. The list is fixed in `app/core/middleware.py`;
it is not a setting. If you serve under your own domain, either edit that list
or have your reverse proxy send a `Host` the list accepts.

## Logs and metrics

- Logging is fixed at INFO in `main.py`. `DEBUG=true` turns on FastAPI's debug
  mode (and auto-reload when started with `python main.py`); it does not change
  the log level.
- Useful startup lines: the placeholder, CORS and rate-limiter errors above,
  `Record storage is durable` / `NOT durable`, `Rate limiting enabled: ...`,
  and `Metrics enabled at /metrics`.
- `/metrics` requires `X-API-Key` when auth is enabled. It is served by
  prometheus-fastapi-instrumentator and exposes its default series:
  `http_requests_total`, `http_request_size_bytes`, `http_response_size_bytes`,
  `http_request_duration_seconds` and `http_request_duration_highr_seconds`,
  plus the standard `process_*` and `python_*` collectors. Headwater defines no
  custom metrics.

## Getting help

When reporting an issue, include:

- The Headwater version (`/status`) and how you run it (Compose, `docker run`,
  bare uvicorn, number of workers).
- The body of `/health/detailed`.
- The failing request, the status code and the response body.
- The relevant log lines. Proxy passwords are masked, but check for other
  secrets before posting.

Further reading: [API Reference](API_REFERENCE.md),
[Deployment](DEPLOYMENT.md), [Performance Tuning](PERFORMANCE_TUNING.md),
[Security Guidelines](SECURITY_GUIDELINES.md). Issues go to
[GitHub](https://github.com/HouseofLoops/headwater/issues).
