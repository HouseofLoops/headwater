# Headwater

One self-hosted API for Google Maps, News, Trends and Autocomplete, plus YouTube
transcripts. Normalised JSON, no per-call vendor pricing, runs in Docker.

[![GitHub release](https://img.shields.io/github/v/release/rainmanjam/headwater)](https://github.com/rainmanjam/headwater/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.14](https://img.shields.io/badge/python-3.14-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)

```bash
git clone https://github.com/rainmanjam/headwater.git && cd headwater
cp .env.example .env          # set API_KEY
docker compose up -d
curl -H "X-API-Key: $API_KEY" \
  "http://localhost:8000/api/v1/google-autocomplete/autocomplete?q=n8n"
```

```json
{"suggestions":["n8n","n8n workflows","n8n ai","n8n pricing","n8n github"]}
```

---

## Why Headwater

These five sources have no single official API between them. Getting search
interest, a place's reviews, a news feed and a video transcript into one pipeline
normally means four vendors, four auth schemes, four response shapes and four
invoices that scale per call.

Headwater is the one service in front of all of them:

- **One key, one base URL, one JSON convention** across 67 operations.
- **Self-hosted.** Your infrastructure, your IP, your rate limits. No per-request
  billing and no third party holding your query history.
- **Built for pipelines, not dashboards.** Every response is flat JSON meant to be
  consumed by n8n, an LLM step or a cron job.
- **Honest about scraping.** Rate limiting, per-host proxy routing, caching and
  politeness pacing are first-class, because the upstreams are real services that
  will block you if you behave badly.

It is not a Google Cloud wrapper. Nothing here needs a Google API key, and nothing
here is an officially supported Google interface.

## Install

### Docker Compose (recommended)

```bash
git clone https://github.com/rainmanjam/headwater.git && cd headwater
cp .env.example .env
docker compose up -d
```

The stack is the API plus Redis. Redis is not optional in production: without it,
Maps jobs, monitors and webhooks fall back to in-memory storage that is lost on
restart and invisible to sibling workers. `/health/detailed` reports
`record_storage_durable` so you can assert on this rather than hope.

### One-line installer

```bash
curl -fsSL https://raw.githubusercontent.com/rainmanjam/headwater/main/scripts/install.sh | sudo bash
```

Installs Docker if absent, configures Redis, sets secure defaults, optionally
issues a Let's Encrypt certificate, and writes update/backup/uninstall helpers to
`/opt/headwater/scripts/`.

### Verify

```bash
curl http://localhost:8000/health
curl -H "X-API-Key: $API_KEY" http://localhost:8000/health/detailed
```

Interactive docs are at `/api/docs` (Swagger) and `/api/redoc`, and the raw
schema at `/openapi.json`. Those are generated from the code, so they are always
the authoritative endpoint list.

### Image signatures

Published images (`rainmanjam/headwater`, `ghcr.io/rainmanjam/headwater`) are
signed keylessly by CI and carry an SPDX SBOM attestation. Check one with
`make docker-verify IMAGE=ghcr.io/rainmanjam/headwater TAG=<version>` (needs
cosign 3 or newer), or see [Verifying images](docs/DOCKERHUB.md#verifying-images)
for the raw `cosign` commands and notes on older releases.

## What you get back

Every example below is a real response from a running instance, trimmed for
length. All requests need `X-API-Key`.

### Google Maps — places with coordinates, ratings and review counts

```bash
curl -H "X-API-Key: $API_KEY" \
  "http://localhost:8000/api/v1/google-maps/search?query=bakery+Austin+Texas&max_results=2"
```

```json
{
  "success": true,
  "query": "bakery Austin Texas",
  "total_results": 2,
  "places": [
    {
      "place_id": "0x8644ca76b9c9106d:0x3e0558783ef8b48b",
      "name": "Quack's 43rd Street Bakery",
      "address": "411 E 43rd St, Austin, TX 78751",
      "phone": "(512) 453-3399",
      "website": "https://quacks43rd.com/",
      "latitude": "30.339001",
      "longitude": "-97.7691042",
      "rating": 4.5,
      "review_count": 1781
    }
  ],
  "job_id": "e1cccb14-e10c-411f-a196-0fe157bbeb26"
}
```

Maps is the expensive surface: roughly **12 seconds per result**, because each
place is opened and read in turn. Ten results is about two minutes. Results are
cached for an hour, so a repeated query returns immediately. `max_results` is
capped at 45 and an unaffordable `max_results`/`timeout` pair is rejected with a
400 that tells you what you can afford, rather than timing out five minutes later.

### YouTube transcripts — full text with timings

```bash
curl -H "X-API-Key: $API_KEY" \
  "http://localhost:8000/api/v1/youtube-transcripts/get-transcript?video_id=-mc6-uem7vM"
```

```json
{
  "video_id": "-mc6-uem7vM",
  "language": "English",
  "language_code": "en",
  "is_generated": true,
  "is_translatable": true,
  "transcript": [
    { "text": "In my last Technicium tutorial, I built", "start": 0.08, "duration": 4.88 }
  ]
}
```

That video returns 529 segments in about two seconds. `POST
/batch-get-transcripts` takes up to 50 ids as a JSON body, and
`/translate-transcript` returns a translated track where one is offered.

### Google News — real publisher URLs, not Google redirects

```bash
curl -H "X-API-Key: $API_KEY" \
  "http://localhost:8000/api/v1/google-news/search/?query=n8n"
```

```json
{
  "articles": [
    {
      "title": "How to use nexos.ai with n8n on Hostinger VPS",
      "published_date": "Tue, 15 Sep 2026 18:30:28 GMT",
      "description": "How to use nexos.ai with n8n on Hostinger VPS",
      "url": "https://www.hostinger.com/support/how-to-use-nexos-ai-with-n8n",
      "publisher": "Hostinger"
    }
  ]
}
```

Google News hands out `news.google.com` redirect links. Headwater decodes them to
the publisher's own URL, which is what you actually want to store or fetch.

Search and topic endpoints return metadata only. For body text, pass a decoded URL
to `/article-details/`, which extracts title, authors, publish date, full text and
keywords. That endpoint refuses any host not on `NEWS_ARTICLE_ALLOWED_HOSTS`: it
fetches arbitrary URLs, so it is deliberately an allow-list rather than a
deny-list.

### Google Trends — interest, related terms and reference data

```bash
curl -H "X-API-Key: $API_KEY" \
  "http://localhost:8000/api/v1/google-trends/geo?find=Tokyo"
```

```json
{ "data": [{ "name": "Tokyo", "id": "13" }] }
```

`/geo` resolves 3,681 locations and `/categories` 1,133 categories, both cached for
a day since they change on the order of months. `/interest-over-time`,
`/related-queries`, `/related-topics` and `/trending-now` cover the live series.

Google enforces its own quota on related queries and topics; those endpoints
return **502** when it is exhausted rather than pretending the data was empty.

### Google Autocomplete — keyword expansion

```bash
curl -H "X-API-Key: $API_KEY" \
  "http://localhost:8000/api/v1/google-autocomplete/autocomplete?q=n8n"
```

```json
{
  "suggestions": ["n8n", "n8n workflows", "n8n ai", "n8n pricing", "n8n github",
                  "n8n cloud", "n8n login", "n8n careers", "n8n meaning"]
}
```

## Endpoints

67 operations across 60 paths. The tables in this README would drift, so the
authoritative list lives at **`/api/docs`** on your running instance.

| Surface | Operations | What it covers |
|---|---:|---|
| `google-maps` | 36 | search, nearby, grid and bounding-box search, place details, reviews, photos, Q&A, menus, popular times, plus async jobs, monitors and webhooks |
| `google-trends` | 10 | interest over time and by region, related queries and topics, trending now, geo and category reference data |
| `google-news` | 9 | search, top stories, by topic, by source, by location, and full-article extraction |
| `youtube-transcripts` | 5 | fetch, list, format, translate, batch |
| `google-autocomplete` | 1 | search suggestions |
| health / status | 6 | `/health`, `/health/detailed`, `/ping`, `/status`, `/api-config`, `/config-sources` |

Most Maps endpoints accept both `GET` (query parameters) and `POST` (JSON body).
Long Maps work can run asynchronously: pass `wait_for_results=false` to get a
`job_id`, then poll `/jobs/{job_id}` and read `/jobs/{job_id}/results`.

## Configuration

Set these in `.env`. See `.env.example` for the full list.

| Variable | Purpose |
|---|---|
| `API_KEY` | Required. Sent as `X-API-Key` on every request. |
| `REDIS_URL` | Cache and durable record storage. |
| `ENABLE_PROXY` | Turn outbound proxying on. |
| `PROXY_URLS` | Comma-separated proxy URLs, rotated round-robin. `PROXY_URL` is accepted as a legacy alias. |
| `NO_PROXY_HOSTS` | Hosts that must bypass the proxy. Suffix match on a dot boundary. |
| `NEWS_ARTICLE_ALLOWED_HOSTS` | Hosts `/article-details/` may fetch. |
| `RATE_LIMIT_ENABLED`, `RATE_LIMIT_REQUESTS`, `RATE_LIMIT_TIMEFRAME` | Request throttling. |
| `CORS_ORIGINS` | Explicit allow-list. A wildcard disables credentialed cross-origin requests. |

### Proxying is per host, not all-or-nothing

`ENABLE_PROXY` used to be global, which forced one choice for every upstream. It
is not one decision, because the upstreams disagree:

- **Reddit and similar** answer `429` to datacentre IPs and need a proxy.
- **YouTube** is refused by some providers at the tunnel. Bright Data returns
  `policy_20050`, "target site requires special permission", on `youtube.com` —
  an account-level compliance gate, so no zone type avoids it.
- **Google Maps** loads fine through a plain `GET` but a full browser navigation
  through a datacentre proxy never settles, so scraping it must go direct.

`NO_PROXY_HOSTS` resolves that. A sensible starting point:

```dotenv
ENABLE_PROXY=true
PROXY_URLS=http://user:pass@proxy.example.com:8080
NO_PROXY_HOSTS=youtube.com,youtu.be,ytimg.com,google.com
```

Proxy credentials are masked in logs. Never log a proxy URL yourself: it carries
`user:pass@` inline, and truncating it is not redaction.

## Operating notes

**Caching.** Redis-backed, per endpoint. Reference data lives a day; Maps searches
an hour; trend series follow the default. Repeated identical requests are cheap;
the first one is not.

**Rate limiting.** Applies per key and returns `429` with the seconds remaining.
It exists to keep you inside the upstreams' tolerance, so raising it is a decision
about their patience, not just yours.

**Errors** follow RFC 7807. A `502` means an upstream genuinely failed; an empty
result set is a `200` with an empty list. The distinction is deliberate — a quiet
week and a broken scraper should never look the same.

**Observability.** Prometheus metrics, `/health/detailed` with per-dependency
status including Redis and record durability, and structured logs.

## Documentation

| Guide | |
|---|---|
| [API reference](docs/API_REFERENCE.md) | Endpoint detail beyond `/api/docs` |
| [Deployment](docs/DEPLOYMENT.md) | Production deployment and secrets |
| [Performance tuning](docs/PERFORMANCE_TUNING.md) | Caching, concurrency, proxy pools |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Symptoms and causes |
| [Security guidelines](docs/SECURITY_GUIDELINES.md) | Hardening and key handling |
| [Architecture](docs/ARCHITECTURE_OVERVIEW.md) | How the pieces fit |
| [Contributing](docs/CONTRIBUTING.md) | Development setup and conventions |
| [Examples](docs/EXAMPLES.md) | Longer worked examples |

## Limits worth knowing before you adopt it

- **Maps costs about 12s per result** and is the slowest thing here by an order of
  magnitude. Plan around the cache, or use the async job endpoints.
- **These are unofficial interfaces.** Google changes its markup and parameters
  without notice. The Maps scraper reports `selectors_stale` when extraction stops
  matching, so breakage surfaces as a signal rather than as silently empty results.
- **Upstream quotas are real.** Google Trends limits related queries and topics
  independently of anything configured here.
- **Some sources need a proxy and some are broken by one.** See the proxy section;
  there is no single setting that is right for every host.

## Contributing

See [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md). Issues and pull requests welcome.

## License

MIT — see [LICENSE](LICENSE).
