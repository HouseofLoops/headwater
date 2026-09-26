# API Usage Examples

Working requests against a local Headwater (`http://localhost:8000`). Parameters
and defaults match the OpenAPI schema; the full list is in
[API_REFERENCE.md](API_REFERENCE.md) and at `/docs` (outside production).

## Contents

- [Authentication](#authentication)
- [Google News](#google-news)
- [Google Trends](#google-trends)
- [Google Autocomplete](#google-autocomplete)
- [YouTube Transcripts](#youtube-transcripts)
- [Google Maps](#google-maps)
- [Python](#python)
- [JavaScript](#javascript)
- [Errors](#errors)

## Authentication

Every `/api/v1/*` request needs one of the keys from `API_KEYS` in the
`X-API-Key` header (header names are case-insensitive):

```bash
export API_KEY="your-key"
```

## Google News

News paths end with a slash. Without it FastAPI answers with a redirect, so add
`-L` or keep the slash.

### Search

Parameters: `query` (required), `language` (default `en`), `country` (default
`US`), `max_results` (default 5), `sort_by` (default `relevance`), `start_date`,
`end_date`, `exclude_duplicates`, `exact_match`.

```bash
curl -G "http://localhost:8000/api/v1/google-news/search/" \
  -H "X-API-Key: $API_KEY" \
  --data-urlencode "query=artificial intelligence" \
  -d country=US -d language=en -d max_results=5
```

Response (`NewsResponse`; values illustrative):

```json
{
  "articles": [
    {
      "title": "Example headline",
      "published_date": "Mon, 14 Sep 2025 08:00:00 GMT",
      "description": "First lines of the article...",
      "url": "https://publisher.example/article",
      "publisher": "Example Publisher"
    }
  ]
}
```

`partial` and `dropped` are added only when some articles could not be returned.

### Top stories

```bash
curl "http://localhost:8000/api/v1/google-news/top/?country=US&language=en&max_results=10" \
  -H "X-API-Key: $API_KEY"
```

Other news endpoints: `/topic/`, `/location/`, `/source/`, `/articles/`,
`/article-details/`, `/available-countries/`, `/available-languages/`.

## Google Trends

Trends responses are `{"data": ...}`, or `{"data": [], "message": "..."}` when
Google returns nothing.

```bash
# Trending searches for a country (geo defaults to US)
curl "http://localhost:8000/api/v1/google-trends/trending-now?geo=US" \
  -H "X-API-Key: $API_KEY"

# Compare keywords over time: comma-separated keywords, timeframe default "today 12-m"
curl -G "http://localhost:8000/api/v1/google-trends/interest-over-time" \
  -H "X-API-Key: $API_KEY" \
  -d keywords=python,javascript,rust -d geo=US --data-urlencode "timeframe=today 3-m"

# Related queries for a single keyword
curl -G "http://localhost:8000/api/v1/google-trends/related-queries" \
  -H "X-API-Key: $API_KEY" \
  --data-urlencode "keyword=artificial intelligence" -d geo=US
```

## Google Autocomplete

Parameters include `q` (required), `output` (`toolbar` default, `chrome`,
`firefox`, `xml`, `safari`, `opera`), `gl` (default `US`), `hl` (default `en`),
`ds` and `variations`.

```bash
curl -G "http://localhost:8000/api/v1/google-autocomplete/autocomplete" \
  -H "X-API-Key: $API_KEY" \
  --data-urlencode "q=python programming" -d output=chrome -d gl=US -d hl=en
```

The body depends on `output`. With `toolbar`/`xml` it is `{"suggestions": [...]}`.
With `chrome`/`firefox` it also carries `original_query`, `descriptions`,
`query_completions`, `metadata`, `raw_response` and `response_metadata`:

```json
{
  "response_type": "json",
  "original_query": "python programming",
  "suggestions": ["python programming language", "python programming for beginners"],
  "response_metadata": {"response_time_seconds": 0.21, "timestamp": "2025-09-14T10:30:00"}
}
```

(trimmed). `variations=true` returns `{"success": true, "message": ..., "keyword_data": ..., "response_metadata": ...}`
instead:

```bash
curl -G "http://localhost:8000/api/v1/google-autocomplete/autocomplete" \
  -H "X-API-Key: $API_KEY" \
  --data-urlencode "q=seo tools" -d variations=true -d gl=US
```

## YouTube Transcripts

```bash
# Transcript; languages is repeatable and defaults to en
curl "http://localhost:8000/api/v1/youtube-transcripts/get-transcript?video_id=dQw4w9WgXcQ&languages=en" \
  -H "X-API-Key: $API_KEY"
```

Response (`TranscriptResponse`, trimmed):

```json
{
  "video_id": "dQw4w9WgXcQ",
  "language": "English",
  "language_code": "en",
  "is_generated": false,
  "is_translatable": true,
  "translation_languages": [{"language": "Spanish", "language_code": "es"}],
  "transcript": [
    {"text": "...", "start": 0.0, "duration": 3.5}
  ]
}
```

```bash
# Available transcripts: {"transcripts": [{video_id, language, language_code,
#   is_generated, is_translatable, translation_languages}, ...]}
curl "http://localhost:8000/api/v1/youtube-transcripts/list-transcripts?video_id=dQw4w9WgXcQ" \
  -H "X-API-Key: $API_KEY"

# Translate
curl "http://localhost:8000/api/v1/youtube-transcripts/translate-transcript?video_id=dQw4w9WgXcQ&target_language=es" \
  -H "X-API-Key: $API_KEY"

# Batch (at most 50 ids); returns a list of TranscriptResponse
curl -X POST "http://localhost:8000/api/v1/youtube-transcripts/batch-get-transcripts" \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"video_ids": ["dQw4w9WgXcQ"], "languages": ["en"]}'
```

## Google Maps

```bash
# Blocking search (waits up to `timeout` seconds, default 300)
curl -G "http://localhost:8000/api/v1/google-maps/search" \
  -H "X-API-Key: $API_KEY" \
  --data-urlencode "query=coffee in Seattle" -d max_results=20

# Same search as a background job: returns a job id immediately
curl -X POST "http://localhost:8000/api/v1/google-maps/search?wait_for_results=false" \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"query": "coffee in Seattle", "max_results": 20}'

# Poll and fetch the job (jobs are visible only to the key that created them)
curl "http://localhost:8000/api/v1/google-maps/jobs/<job_id>" -H "X-API-Key: $API_KEY"
curl "http://localhost:8000/api/v1/google-maps/jobs/<job_id>/results" -H "X-API-Key: $API_KEY"
```

## Python

With `httpx` (already a Headwater dependency):

```python
import httpx

BASE = "http://localhost:8000/api/v1"
client = httpx.Client(base_url=BASE, headers={"X-API-Key": "your-key"}, timeout=60)

news = client.get("/google-news/search/", params={"query": "machine learning", "max_results": 10})
news.raise_for_status()
for article in news.json()["articles"]:
    print(article["publisher"], article["title"])

trends = client.get("/google-trends/interest-over-time", params={"keywords": "python,javascript", "geo": "US"})
print(trends.json()["data"])

transcript = client.get("/youtube-transcripts/get-transcript", params={"video_id": "dQw4w9WgXcQ"})
print(len(transcript.json()["transcript"]), "segments")
```

Concurrent requests, with a simple retry on 429 using `Retry-After`:

```python
import asyncio

import httpx


async def get(client: httpx.AsyncClient, path: str, params: dict) -> dict:
    for _ in range(3):
        response = await client.get(path, params=params)
        if response.status_code == 429:
            await asyncio.sleep(int(response.headers.get("Retry-After", "1")))
            continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError(f"still rate limited: {path}")


async def main() -> None:
    async with httpx.AsyncClient(
        base_url="http://localhost:8000/api/v1", headers={"X-API-Key": "your-key"}, timeout=60
    ) as client:
        queries = ["artificial intelligence", "data science", "python programming"]
        results = await asyncio.gather(
            *(get(client, "/google-news/search/", {"query": q}) for q in queries), return_exceptions=True
        )
        for query, result in zip(queries, results):
            if isinstance(result, Exception):
                print(f"{query}: failed ({result})")
            else:
                print(f"{query}: {len(result['articles'])} articles")


asyncio.run(main())
```

## JavaScript

```javascript
const BASE_URL = 'http://localhost:8000/api/v1';
const API_KEY = 'your-key';

async function apiGet(path, params = {}) {
  const url = new URL(BASE_URL + path);
  for (const [key, value] of Object.entries(params)) url.searchParams.append(key, value);
  const response = await fetch(url, { headers: { 'X-API-Key': API_KEY } });
  const body = await response.json();
  if (!response.ok) {
    // RFC 7807 body, except FastAPI's 422 which is {"detail": [...]}
    throw new Error(`${response.status}: ${body.title ?? ''} ${JSON.stringify(body.detail)}`);
  }
  return body;
}

const news = await apiGet('/google-news/search/', { query: 'artificial intelligence', max_results: 5 });
console.log(news.articles.length, 'articles');

const trending = await apiGet('/google-trends/trending-now', { geo: 'US' });
console.log(trending.data);
```

## Errors

Errors (except request validation) are RFC 7807 `application/problem+json`:

```json
{
  "type": "https://headwater.com/problems/rate_limit_exceeded",
  "title": "Too Many Requests",
  "status": 429,
  "detail": "..."
}
```

| Status | Cause |
|--------|-------|
| 401 | Missing or unknown `X-API-Key` |
| 422 | Invalid or missing parameters (FastAPI `{"detail": [...]}` body) |
| 429 | Rate limit reached; honour `Retry-After`. Type `upstream_rate_limited` (with `upstream` and `retry_after`) means Google is throttling Headwater rather than you exceeding your quota |
| 502 / 503 | Upstream failure or block, or rate limiter backend unavailable |

For more, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
