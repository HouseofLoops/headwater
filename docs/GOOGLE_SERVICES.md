# Google Services Integration

How Headwater gets its data from each Google service. None of these use an
official Google API or a Google API key: every source is a public web endpoint
or page, reached with the libraries below. There are no Google credentials to
configure.

## Sources

| API prefix | Upstream | How it is fetched | Code |
|------------|----------|-------------------|------|
| `/api/v1/google-maps` | `https://www.google.com/maps` | Headless Chromium driven by Playwright | `app/services/google_maps/`, `app/services/google_maps_service.py`, `app/services/google_maps_scraper.py`, `app/services/google_maps_monitors.py` |
| `/api/v1/google-news` | Google News RSS and `news.google.com` | `gnews`; article bodies via `newspaper4k`; Google News redirect URLs decoded through `news.google.com` | `app/services/google_news_service.py`, `app/services/google_news_article_service.py`, `app/services/google_news_catalog.py` |
| `/api/v1/google-trends` | Google Trends | `trendspy` | `app/api/google_trends/google_trends_api.py` |
| `/api/v1/google-autocomplete` | `https://www.google.com/complete/search` | Direct HTTP through the shared `httpx` client | `app/services/google_autocomplete_service.py`, `app/api/google_autocomplete/google_autocomplete_api.py` |
| `/api/v1/youtube-transcripts` | YouTube | `youtube-transcript-api` | `app/services/youtube_transcripts_service.py` |

Library versions are pinned in `requirements.txt` and, with hashes, in
`requirements.lock`.

## Upstream limits

These upstreams publish no quotas for this kind of access. They throttle or block
by IP when traffic looks automated, and the thresholds are not documented. In
practice:

- Datacentre IPs are throttled more readily than residential ones.
- Headwater aims to report an upstream failure as an error response
  (`application/problem+json`) rather than as an empty success; see
  `tests/test_failure_honesty.py`.
- Google page and response formats change without notice, so parsers can break
  between releases.

## Reducing upstream load

| Setting | Default | Effect |
|---------|---------|--------|
| `ENABLE_CACHE` / `CACHE_TTL` | `true` / `3600` | Identical requests within the TTL are served from cache (Redis when `REDIS_URL` is set, memory otherwise) |
| `RATE_LIMIT_REQUESTS` / `RATE_LIMIT_TIMEFRAME` | `100` / `3600` | Caps how fast clients can drive upstream traffic |
| `ENABLE_PROXY` / `PROXY_URLS` | `false` / unset | Rotates outbound requests round-robin across the listed proxies (`app/core/proxy.py`) |
| `NO_PROXY_HOSTS` | unset | Hosts that always go direct. Google Maps browser navigation does not complete through some datacentre proxies, and some proxy providers refuse YouTube |
| `AUTOCOMPLETE_MAX_PARALLEL_REQUESTS` | `10` | Parallel upstream calls when `variations=true` |
| `GOOGLE_MAPS_MAX_CONCURRENT_BROWSERS` | `4` | Concurrent Chromium instances (environment variable, not a `Settings` field) |

`make test-proxy` checks a proxy given in `PROXY_URL`; `make test-apis` checks that
Google News, Google Trends and YouTube are reachable from the host.

## Using the services

Call the HTTP API; see [API_REFERENCE.md](API_REFERENCE.md) for parameters and
[EXAMPLES.md](EXAMPLES.md) for requests. The interactive schema is served at
`/docs` outside production.
