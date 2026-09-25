# Performance Tuning Guide

This guide provides recommendations for optimizing the performance of the Headwater API.

Headwater is a thin layer over upstream services (Google Maps, News, Trends and
Autocomplete, and YouTube). Almost all request time is spent waiting on those
upstreams, and on Chromium for Maps. The levers that matter are, in order:
caching, Maps browser concurrency, worker count, and proxies. There is no
database to tune.

Every setting below is a field on `Settings` in `app/core/config.py`, with the
default shown, unless it is marked as read directly from the environment.
Settings are loaded once per process; restart after changing them.

## Table of Contents

- [Where the time goes](#where-the-time-goes)
- [Caching](#caching)
- [Workers and Redis](#workers-and-redis)
- [Google Maps concurrency](#google-maps-concurrency)
- [Outbound HTTP connections](#outbound-http-connections)
- [Autocomplete fan-out](#autocomplete-fan-out)
- [Proxies](#proxies)
- [Rate limiting and throughput](#rate-limiting-and-throughput)
- [Container resources](#container-resources)
- [Measuring](#measuring)
- [Settings with no current effect](#settings-with-no-current-effect)
- [Checklist](#checklist)

## Where the time goes

| Service | What a request does | Typical cost driver |
|---------|---------------------|---------------------|
| Google Maps search | Drives headless Chromium and opens each place in turn | About 12 seconds per result |
| Google Maps place endpoints | One browser session per request | Page load time |
| Google News | Fetches and parses RSS; `article-details` also fetches the publisher page | Upstream latency |
| Google Trends | Calls Google Trends through trendspy in a thread pool | Upstream latency and throttling |
| Google Autocomplete | One HTTP call, or many in parallel with `variations=true` | Upstream latency |
| YouTube transcripts | HTTP calls through youtube-transcript-api | Upstream latency, IP blocking |

## Caching

Caching is on by default and is the single largest gain: a cache hit skips the
upstream entirely.

| Setting | Default | Effect |
|---------|---------|--------|
| `ENABLE_CACHE` | `true` | Turns the response cache on or off |
| `CACHE_TTL` | `3600` | TTL in seconds for anything that does not set its own |
| `REDIS_URL` | unset | When set, the cache lives in Redis and is shared by every worker |

Without Redis the cache is a per-process dictionary. Each worker warms its own
copy, there is no size cap, and expired entries are only removed when they are
read. On a long-running single worker with many distinct queries, that memory
grows; use Redis.

Per-endpoint TTLs are set in code, not in settings:

| Endpoint group | TTL |
|----------------|-----|
| News `top` | 5 minutes |
| News `source`, `topic`, `location`, `articles` | 10 minutes |
| News `search` | `CACHE_TTL` |
| News `article-details` | 1 hour (not cached when the NLP step failed transiently) |
| Trends `categories`, `geo` | 24 hours |
| Other Trends endpoints | `CACHE_TTL` |
| Autocomplete | `CACHE_TTL` |
| YouTube transcripts | `CACHE_TTL` |
| Maps `GET /search` with `wait_for_results=true` | 1 hour |

`POST /api/v1/google-maps/search` and `wait_for_results=false` searches are not
cached. If clients repeat the same Maps searches, have them use the GET form.

Upstream failures are not cached, so a failed request is retried on the next
call rather than pinned for the TTL.

Raising `CACHE_TTL` trades freshness for fewer upstream calls. Trend series and
transcripts change slowly and tolerate longer TTLs than news.

## Workers and Redis

The image runs a single uvicorn worker:

```
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
```

The `--workers 1` flag on the command line takes precedence over
`WEB_CONCURRENCY`, so to run more workers override the command, for example in
`docker-compose.yml`:

```yaml
services:
  web:
    command: ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

Before adding workers:

- **Configure `REDIS_URL`.** Without it, each worker has its own cache, its own
  rate-limit counters (so limits are multiplied by the worker count), and its
  own in-memory store for Maps jobs, monitors and webhooks (so a job created on
  one worker is a 404 on another). With `ENVIRONMENT` set to `production`,
  `prod` or `staging` and rate limiting on, the app refuses to start with
  several workers and no Redis.
- **Budget memory for Maps.** The browser cap below is per worker.
- **Each worker starts its own Maps monitor scheduler.** The code has no
  cross-worker coordination for it, so check monitor and webhook behaviour
  before running several workers with monitors in use.

Most time is spent awaiting upstreams, so a single async worker already handles
many concurrent requests. Add workers when CPU on one core is the bottleneck,
not by default.

The Redis client itself uses a pool of up to 20 connections with 5 second
connect and socket timeouts, fixed in `app/core/redis_manager.py`.

## Google Maps concurrency

Two limits are read directly from the environment (not through `Settings`) in
`app/services/google_maps/scraper_limits.py`:

| Variable | Default | Effect |
|----------|---------|--------|
| `GOOGLE_MAPS_MAX_CONCURRENT_BROWSERS` | `4` | Most Chromium instances alive at once, per worker process. Further requests wait for a slot |
| `GOOGLE_MAPS_MAX_FANOUT` | `25` | Most points a grid search or bulk search will visit. Longer lists are truncated and the truncation is logged |

A non-integer or value below 1 logs a warning and falls back to the default.

Chromium uses roughly 100 MB per instance. Too low a browser cap makes requests
wait; too high a cap gets the container OOM-killed mid-request. Size it as
`workers x GOOGLE_MAPS_MAX_CONCURRENT_BROWSERS x ~100 MB`, plus the app itself,
and keep that under the container memory limit.

Request-level controls:

- `max_results` defaults to 20 and is capped at 45. At about 12 seconds per
  result, a 20-result search takes around four minutes.
- `timeout` defaults to 300 seconds and accepts 30 to 600. A `max_results` that
  cannot fit in the timeout is rejected with 400 before any browser starts, and
  the message suggests a size that fits.
- `wait_for_results=false` returns a job ID immediately; the client polls
  `/api/v1/google-maps/jobs/{job_id}`. This frees the HTTP connection but uses
  the same browser slots.
- `depth` and `email_extraction` are accepted but not used by the native
  scraper, so they do not change the cost of a search.

## Outbound HTTP connections

Autocomplete and News share a pooled `httpx` client, one per proxy URL.
YouTube uses its own `requests` session but reads the same timeouts.

| Setting | Default | Used for |
|---------|---------|----------|
| `HTTP_CONNECTION_POOL_SIZE` | `20` | `max_connections` of each pooled client |
| `HTTP_MAX_KEEPALIVE_CONNECTIONS` | `10` | Idle connections kept open per client |
| `HTTP_CONNECTION_TIMEOUT` | `10.0` | Connect timeout (pooled client and YouTube) |
| `HTTP_READ_TIMEOUT` | `30.0` | Read timeout (pooled client, YouTube, News article fetch and parse) |

Raise the pool size if you run many parallel Autocomplete variations or News
requests per worker. Lower the read timeout to fail faster against a slow
upstream; raise it if YouTube returns 504 through a slow proxy. Write (10 s) and
pool-acquire (5 s) timeouts and keep-alive expiry (30 s) are fixed in
`app/core/http_client.py`.

## Autocomplete fan-out

| Setting | Default | Effect |
|---------|---------|--------|
| `AUTOCOMPLETE_MAX_PARALLEL_REQUESTS` | `10` | Parallel upstream calls when `variations=true` |

Higher values finish a variations request sooner but send Google a burst of
calls from one IP, which invites throttling. Keep it at or below
`HTTP_CONNECTION_POOL_SIZE`.

## Proxies

Proxies are for getting past IP blocking, not for speed; each proxy adds
latency.

| Setting | Default | Effect |
|---------|---------|--------|
| `ENABLE_PROXY` | `false` | Master switch |
| `PROXY_URLS` | unset | Comma-separated list |
| `PROXY_URL` | unset | Single proxy, merged with `PROXY_URLS` |
| `NO_PROXY_HOSTS` | unset | Domain suffixes that go direct (Maps and YouTube only) |

- Autocomplete, Trends and News rotate through `PROXY_URLS` round-robin, which
  spreads load across proxy IPs.
- Maps and YouTube always use the first proxy in the list.
- Google Maps browser sessions through a datacentre proxy tend not to complete.
  If Maps is slow or hangs only with proxying on, set `NO_PROXY_HOSTS=google.com`.
- The pooled HTTP client is created per proxy URL, so a long proxy list means
  more pools. Each is bounded by the pool settings above.

## Rate limiting and throughput

| Setting | Default |
|---------|---------|
| `RATE_LIMIT_ENABLED` | `true` |
| `RATE_LIMIT_REQUESTS` | `100` |
| `RATE_LIMIT_TIMEFRAME` | `3600` (seconds) |

The defaults are conservative. When sizing them:

- Requests to `/api/v1/...` count twice against the bucket (middleware and
  per-route check), so a key gets about 50 API calls per hour by default.
- `/health` counts too. The container health check alone makes 120 requests an
  hour from `localhost`, which exceeds the default of 100.
- Counters live in Redis when `REDIS_URL` is set. Each check is one Redis round
  trip, so keep Redis close to the app.

See [Troubleshooting: Rate limiting](TROUBLESHOOTING.md#rate-limiting) for the
full behaviour.

## Container resources

`docker-compose.yml` sets these limits:

| Service | CPU limit | Memory limit | Memory reservation |
|---------|-----------|--------------|--------------------|
| `web` | 2.0 | 2G | 512M |
| `redis` | 0.5 | 256M | 64M |

Redis runs with `--appendonly yes` and a named volume, so cached data and Maps
records survive a Redis restart. If you raise `CACHE_TTL` substantially or run
heavy traffic, watch Redis memory against its 256M limit.

The `web` memory limit is what bounds Maps concurrency in practice. With one
worker and the default of 4 browsers there is headroom; raise the limit before
raising workers or browsers.

## Measuring

- `/metrics` (requires `X-API-Key` when auth is on) exposes the
  prometheus-fastapi-instrumentator defaults: `http_requests_total`,
  `http_request_duration_seconds`, `http_request_duration_highr_seconds`,
  `http_request_size_bytes` and `http_response_size_bytes`, labelled by handler,
  method and grouped status, plus `process_*` and `python_*` series. There are
  no cache hit/miss or upstream-specific metrics.
- `/health/detailed` shows Redis ping time and CPU, memory and disk usage.
- Cache hits and misses are logged at DEBUG, but the app logs at INFO, so they
  are not visible by default.
- For load testing, remember the rate limiter: raise `RATE_LIMIT_REQUESTS` or set
  `RATE_LIMIT_ENABLED=false` in the test environment, or you will measure 429s.

## Settings with no current effect

These are declared on `Settings` and appear in `.env.example`, but no request
path uses them today. Changing them does not change behaviour.

| Setting | Default |
|---------|---------|
| `HTTP_MAX_CONNECTIONS_PER_HOST` | `5` |
| `BATCH_SIZE` | `50` |
| `BATCH_PROCESSING_ENABLED` | `true` |
| `BATCH_TIMEOUT` | `60.0` |
| `MAX_CONCURRENT_BATCHES` | `3` |
| `AUTOCOMPLETE_REQUEST_TIMEOUT` | `30` |
| `AUTOCOMPLETE_MAX_RETRIES` | `3` |
| `AUTOCOMPLETE_RETRY_DELAY` | `1.0` |
| `RESPONSE_METADATA_ENABLED`, `INCLUDE_REQUEST_TIMING`, `INCLUDE_CONNECTION_INFO`, `INCLUDE_CACHE_INFO`, `INCLUDE_RATE_LIMIT_INFO` | `true` |

`BATCH_PROCESSING_ENABLED`, `BATCH_TIMEOUT` and `MAX_CONCURRENT_BATCHES` are read
by `batch_requests` in `app/core/http_client.py`, but no endpoint calls it.

## Checklist

- [ ] `REDIS_URL` set, and `record_storage_durable` is `true` in `/health/detailed`
- [ ] `ENABLE_CACHE=true`; `CACHE_TTL` chosen for your freshness needs
- [ ] Clients that repeat Maps searches use `GET /api/v1/google-maps/search`
- [ ] `GOOGLE_MAPS_MAX_CONCURRENT_BROWSERS x workers x ~100 MB` fits the memory limit
- [ ] Large Maps searches use `wait_for_results=false`
- [ ] `RATE_LIMIT_REQUESTS` sized for real traffic, counting `/api/v1` calls twice and the health check
- [ ] Proxies configured only where upstreams block you; `NO_PROXY_HOSTS=google.com` if Maps hangs through them
- [ ] More than one worker only with Redis, and only when one core is saturated

Further reading: [Troubleshooting](TROUBLESHOOTING.md),
[Deployment](DEPLOYMENT.md), [API Reference](API_REFERENCE.md).
