# Improvement Recommendations & Complementary Projects

> **Archived 2026-09-23.** Items still open after checking them against the code are tracked in the
> "Open items" section of [ROADMAP.md](../ROADMAP.md#open-items). Compression, Prometheus metrics and
> dependency health checks from this list have since been implemented.

This document outlines recommended improvements for the Headwater API and suggests open source projects that could be integrated to extend functionality.

## Table of Contents

- [Architecture \& Performance](#architecture--performance)
- [Security \& Authentication](#security--authentication)
- [Observability](#observability)
- [Developer Experience](#developer-experience)
- [Complementary Open Source Projects](#complementary-open-source-projects)
  - [Social Media Data](#social-media-data)
  - [Content Analysis \& NLP](#content-analysis--nlp)
  - [Additional Data Sources](#additional-data-sources)
  - [Web Scraping \& Extraction](#web-scraping--extraction)
  - [AI \& LLM Integration](#ai--llm-integration)
  - [Monitoring \& Infrastructure](#monitoring--infrastructure)
- [High-Priority Additions](#high-priority-additions)
- [Implementation Notes](#implementation-notes)

---

## Architecture & Performance

### 1. Add Request Queuing

Use task queues for long-running operations like batch transcript fetches or trend analysis.

**Recommended Libraries:**
- [Celery](https://github.com/celery/celery) - Distributed task queue
- [ARQ](https://github.com/samuelcolvin/arq) - Async Redis queue (better for FastAPI)
- [Dramatiq](https://github.com/Bogdanp/dramatiq) - Alternative to Celery

**Implementation Example:**
```python
from arq import create_pool
from arq.connections import RedisSettings


async def fetch_transcripts_task(ctx, video_ids: list):
    """Background task for batch transcript fetching."""
    results = []
    for video_id in video_ids:
        result = await fetch_transcript(video_id)
        results.append(result)
    return results
```

### 2. Implement Circuit Breakers

Add resilience patterns to handle external API failures gracefully.

**Recommended Libraries:**
- [pybreaker](https://github.com/danielfm/pybreaker) - Circuit breaker implementation
- [aiobreaker](https://github.com/arlyon/aiobreaker) - Async circuit breaker

**Implementation Example:**
```python
from pybreaker import CircuitBreaker

google_breaker = CircuitBreaker(fail_max=5, reset_timeout=60, name="google_api")


@google_breaker
async def call_google_api():
    # API call logic
    pass
```

### 3. Add Response Compression

Enable gzip/brotli compression for large responses (transcripts, news articles).

**Implementation:**
```python
from fastapi.middleware.gzip import GZipMiddleware

app.add_middleware(GZipMiddleware, minimum_size=1000)
```

### 4. Implement API Versioning

Beyond URL-based versioning, add header-based version selection.

**Implementation:**
```python
from fastapi import Header


async def get_api_version(x_api_version: str = Header(default="1.0")) -> str:
    return x_api_version
```

### 5. Add Pagination

Implement cursor-based pagination for endpoints returning large datasets.

**Implementation Example:**
```python
from pydantic import BaseModel
from typing import Optional, List, Generic, TypeVar

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    items: List[T]
    next_cursor: Optional[str]
    has_more: bool
    total_count: int
```

---

## Security & Authentication

### 6. Add OAuth2/JWT Support

Implement user-based authentication beyond API keys.

**Recommended Libraries:**
- [python-jose](https://github.com/mpdavis/python-jose) - JWT implementation
- [authlib](https://github.com/lepture/authlib) - OAuth client/server

**Implementation Example:**
```python
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=401,
        detail="Could not validate credentials",
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    return username
```

### 7. Implement API Key Scopes

Limit API keys to specific endpoints or operations.

**Implementation Example:**
```python
class APIKeyScope(str, Enum):
    READ_NEWS = "news:read"
    READ_TRENDS = "trends:read"
    READ_TRANSCRIPTS = "transcripts:read"
    ADMIN = "admin"


def require_scope(scope: APIKeyScope):
    def dependency(api_key: str = Depends(get_api_key)):
        if scope not in get_key_scopes(api_key):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return api_key

    return dependency
```

### 8. Add Request Signing

HMAC signatures for request integrity verification.

**Implementation Example:**
```python
import hmac
import hashlib


def verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
```

### 9. Rate Limit by Tier

Different rate limits for different API key tiers.

**Implementation Example:**
```python
RATE_LIMIT_TIERS = {
    "free": {"requests": 100, "period": 3600},
    "pro": {"requests": 1000, "period": 3600},
    "enterprise": {"requests": 10000, "period": 3600},
}
```

---

## Observability

### 10. Add Structured Logging

JSON logs with correlation IDs for distributed tracing.

**Recommended Libraries:**
- [structlog](https://github.com/hynek/structlog) - Structured logging
- [python-json-logger](https://github.com/madzak/python-json-logger) - JSON formatter

**Implementation Example:**
```python
import structlog

structlog.configure(processors=[structlog.processors.TimeStamper(fmt="iso"), structlog.processors.JSONRenderer()])

logger = structlog.get_logger()
logger.info("request_processed", request_id="abc123", duration_ms=45)
```

### 11. Implement Metrics

Prometheus metrics for monitoring.

**Recommended Library:**
- [prometheus-fastapi-instrumentator](https://github.com/trallnag/prometheus-fastapi-instrumentator)

**Implementation Example:**
```python
from prometheus_fastapi_instrumentator import Instrumentator

Instrumentator().instrument(app).expose(app)
```

### 12. Add Distributed Tracing

OpenTelemetry integration for request tracing.

**Recommended Library:**
- [opentelemetry-python](https://github.com/open-telemetry/opentelemetry-python)

**Implementation Example:**
```python
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

FastAPIInstrumentor.instrument_app(app)
```

### 13. Health Check Dependencies

Check Redis and external API connectivity in health endpoints.

**Implementation Example:**
```python
@app.get("/health/detailed")
async def detailed_health():
    checks = {
        "redis": await check_redis_connection(),
        "google_api": await check_google_api(),
        "youtube_api": await check_youtube_api(),
    }
    healthy = all(checks.values())
    return {"status": "healthy" if healthy else "degraded", "checks": checks}
```

---

## Developer Experience

### 14. Add Webhook Support

Async notifications for completed jobs.

**Implementation Example:**
```python
class WebhookConfig(BaseModel):
    url: str
    events: List[str]
    secret: str


async def send_webhook(config: WebhookConfig, event: str, payload: dict):
    signature = hmac.new(config.secret.encode(), json.dumps(payload).encode(), hashlib.sha256).hexdigest()

    async with httpx.AsyncClient() as client:
        await client.post(
            config.url, json={"event": event, "data": payload}, headers={"X-Webhook-Signature": signature}
        )
```

### 15. Implement GraphQL

Alternative query interface for flexible data fetching.

**Recommended Library:**
- [strawberry-graphql](https://github.com/strawberry-graphql/strawberry)

**Implementation Example:**
```python
import strawberry
from strawberry.fastapi import GraphQLRouter


@strawberry.type
class NewsArticle:
    title: str
    description: str
    url: str
    published_at: str


@strawberry.type
class Query:
    @strawberry.field
    async def news(self, query: str, limit: int = 10) -> list[NewsArticle]:
        # Fetch news logic
        pass


schema = strawberry.Schema(Query)
graphql_app = GraphQLRouter(schema)
app.include_router(graphql_app, prefix="/graphql")
```

### 16. Add SDK Generation

Auto-generate client libraries from OpenAPI spec.

**Recommended Tools:**
- [openapi-generator](https://github.com/OpenAPITools/openapi-generator)
- [fern](https://github.com/fern-api/fern)

---

## Complementary Open Source Projects

### Social Media Data

| Project | Description | PyPI | GitHub Stars | Use Case |
|---------|-------------|------|--------------|----------|
| **[snscrape](https://github.com/JustAnotherArchivist/snscrape)** | Social media scraper for Twitter, Reddit, Instagram, etc. | `snscrape` | 4k+ | Multi-platform social data |
| **[praw](https://github.com/praw-dev/praw)** | Python Reddit API Wrapper | `praw` | 3k+ | Reddit posts, comments, trends |
| **[instaloader](https://github.com/instaloader/instaloader)** | Instagram data downloader | `instaloader` | 8k+ | Instagram profiles, posts |
| **[TikTok-Api](https://github.com/davidteather/TikTok-Api)** | Unofficial TikTok API | `TikTokApi` | 4k+ | TikTok trends, videos |
| **[twscrape](https://github.com/vladkens/twscrape)** | Twitter/X scraper | `twscrape` | 1k+ | Twitter data without official API |
| **[ntscraper](https://github.com/bocchilorenzo/ntscraper)** | Nitter scraper for Twitter | `ntscraper` | 200+ | Twitter via Nitter instances |

**Integration Example (praw):**
```python
import praw

reddit = praw.Reddit(client_id="YOUR_CLIENT_ID", client_secret="YOUR_CLIENT_SECRET", user_agent="Headwater/1.0")


def get_trending_subreddits(limit=10):
    return [sub.display_name for sub in reddit.subreddits.popular(limit=limit)]


def get_subreddit_hot_posts(subreddit_name, limit=25):
    subreddit = reddit.subreddit(subreddit_name)
    return [{"title": post.title, "score": post.score, "url": post.url} for post in subreddit.hot(limit=limit)]
```

### Content Analysis & NLP

| Project | Description | PyPI | Use Case |
|---------|-------------|------|----------|
| **[transformers](https://github.com/huggingface/transformers)** | State-of-the-art ML models | `transformers` | Sentiment analysis, summarization |
| **[spaCy](https://github.com/explosion/spaCy)** | Industrial-strength NLP | `spacy` | Entity extraction, text analysis |
| **[textblob](https://github.com/sloria/TextBlob)** | Simple text processing | `textblob` | Quick sentiment scoring |
| **[sumy](https://github.com/miso-belica/sumy)** | Text summarization | `sumy` | Article/transcript summarization |
| **[keybert](https://github.com/MaartenGr/KeyBERT)** | Keyword extraction with BERT | `keybert` | Extract keywords from content |
| **[vader-sentiment](https://github.com/cjhutto/vaderSentiment)** | Rule-based sentiment | `vaderSentiment` | Social media sentiment |
| **[langdetect](https://github.com/Mimino666/langdetect)** | Language detection | `langdetect` | Identify content language |

**Integration Example (KeyBERT):**
```python
from keybert import KeyBERT

kw_model = KeyBERT()


def extract_keywords(text: str, top_n: int = 10):
    keywords = kw_model.extract_keywords(text, keyphrase_ngram_range=(1, 2), stop_words="english", top_n=top_n)
    return [{"keyword": kw, "score": score} for kw, score in keywords]
```

**Integration Example (Sentiment Analysis):**
```python
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

analyzer = SentimentIntensityAnalyzer()


def analyze_sentiment(text: str):
    scores = analyzer.polarity_scores(text)
    return {
        "compound": scores["compound"],
        "positive": scores["pos"],
        "negative": scores["neg"],
        "neutral": scores["neu"],
        "label": "positive" if scores["compound"] > 0.05 else "negative" if scores["compound"] < -0.05 else "neutral",
    }
```

### Additional Data Sources

| Project | Description | PyPI | Use Case |
|---------|-------------|------|----------|
| **[googlesearch-python](https://github.com/Nv7-GitHub/googlesearch)** | Google search scraper | `googlesearch-python` | Web search results |
| **[serpapi](https://github.com/serpapi/google-search-results-python)** | Search engine results API | `google-search-results` | Multiple search engines |
| **[feedparser](https://github.com/kurtmckee/feedparser)** | RSS/Atom feed parser | `feedparser` | News feed aggregation |
| **[podcastparser](https://github.com/gpodder/podcastparser)** | Podcast feed parser | `podcastparser` | Podcast metadata |
| **[yt-dlp](https://github.com/yt-dlp/yt-dlp)** | Video downloader/metadata | `yt-dlp` | Multi-platform video data |
| **[atproto](https://github.com/MarshalX/atproto)** | Bluesky AT Protocol SDK | `atproto` | Bluesky social data |

**Integration Example (feedparser):**
```python
import feedparser


def parse_rss_feed(feed_url: str):
    feed = feedparser.parse(feed_url)
    return {
        "title": feed.feed.get("title"),
        "description": feed.feed.get("description"),
        "articles": [
            {
                "title": entry.get("title"),
                "link": entry.get("link"),
                "published": entry.get("published"),
                "summary": entry.get("summary"),
            }
            for entry in feed.entries
        ],
    }


# Popular news RSS feeds
NEWS_FEEDS = {
    "bbc": "http://feeds.bbci.co.uk/news/rss.xml",
    "cnn": "http://rss.cnn.com/rss/edition.rss",
    "reuters": "https://www.reutersagency.com/feed/",
}
```

### Web Scraping & Extraction

| Project | Description | PyPI | Use Case |
|---------|-------------|------|----------|
| **[trafilatura](https://github.com/adbar/trafilatura)** | Web content extraction | `trafilatura` | Clean article text |
| **[playwright](https://github.com/microsoft/playwright-python)** | Browser automation | `playwright` | JS-rendered content |
| **[crawlee](https://github.com/apify/crawlee-python)** | Web scraping framework | `crawlee` | Large-scale crawling |
| **[readability-lxml](https://github.com/buriy/python-readability)** | Article extraction | `readability-lxml` | Clean article content |
| **[goose3](https://github.com/goose3/goose3)** | Article extractor | `goose3` | Article text/images |

**Integration Example (trafilatura):**
```python
import trafilatura


def extract_article_content(url: str):
    """Extract clean article content from URL."""
    downloaded = trafilatura.fetch_url(url)
    if downloaded is None:
        return None

    # Extract main content
    content = trafilatura.extract(downloaded, include_comments=False, include_tables=True, output_format="json")

    return content


def extract_article_metadata(url: str):
    """Extract article metadata."""
    downloaded = trafilatura.fetch_url(url)
    if downloaded is None:
        return None

    metadata = trafilatura.extract_metadata(downloaded)
    return {
        "title": metadata.title,
        "author": metadata.author,
        "date": metadata.date,
        "description": metadata.description,
        "sitename": metadata.sitename,
    }
```

### AI & LLM Integration

| Project | Description | PyPI | Use Case |
|---------|-------------|------|----------|
| **[litellm](https://github.com/BerriAI/litellm)** | Unified LLM API | `litellm` | Multi-provider LLM access |
| **[langchain](https://github.com/langchain-ai/langchain)** | LLM application framework | `langchain` | Content analysis pipelines |
| **[ollama](https://github.com/ollama/ollama)** | Local LLM runner | `ollama` | Self-hosted AI |
| **[openai](https://github.com/openai/openai-python)** | OpenAI API client | `openai` | GPT integration |
| **[anthropic](https://github.com/anthropics/anthropic-sdk-python)** | Anthropic API client | `anthropic` | Claude integration |
| **[instructor](https://github.com/jxnl/instructor)** | Structured LLM outputs | `instructor` | Type-safe LLM responses |

**Integration Example (LiteLLM):**
```python
from litellm import completion


async def summarize_content(text: str, model: str = "gpt-3.5-turbo"):
    """Summarize content using any LLM provider."""
    response = completion(
        model=model,
        messages=[
            {"role": "system", "content": "Summarize the following text concisely."},
            {"role": "user", "content": text},
        ],
    )
    return response.choices[0].message.content


async def analyze_sentiment_llm(text: str):
    """Analyze sentiment using LLM."""
    response = completion(
        model="gpt-3.5-turbo",
        messages=[
            {
                "role": "system",
                "content": "Analyze the sentiment. Respond with JSON: {sentiment: positive/negative/neutral, confidence: 0-1, reasoning: string}",
            },
            {"role": "user", "content": text},
        ],
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content
```

### Monitoring & Infrastructure

| Project | Description | PyPI | Use Case |
|---------|-------------|------|----------|
| **[prometheus-fastapi-instrumentator](https://github.com/trallnag/prometheus-fastapi-instrumentator)** | FastAPI metrics | `prometheus-fastapi-instrumentator` | Prometheus monitoring |
| **[opentelemetry-python](https://github.com/open-telemetry/opentelemetry-python)** | Distributed tracing | `opentelemetry-api` | Request tracing |
| **[sentry-sdk](https://github.com/getsentry/sentry-python)** | Error tracking | `sentry-sdk` | Exception monitoring |
| **[aiocache](https://github.com/aio-libs/aiocache)** | Async caching | `aiocache` | Multi-backend caching |
| **[slowapi](https://github.com/laurentS/slowapi)** | Rate limiting | `slowapi` | Request throttling |

**Integration Example (Prometheus + Sentry):**
```python
from prometheus_fastapi_instrumentator import Instrumentator
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration

# Sentry error tracking
sentry_sdk.init(
    dsn="YOUR_SENTRY_DSN",
    integrations=[FastApiIntegration()],
    traces_sample_rate=0.1,
)

# Prometheus metrics
instrumentator = Instrumentator(
    should_group_status_codes=True,
    should_instrument_requests_inprogress=True,
    excluded_handlers=["/health", "/metrics"],
)
instrumentator.instrument(app).expose(app, endpoint="/metrics")
```

---

## High-Priority Additions

Based on the current Headwater API capabilities, these integrations would add the most value:

### 1. Twitter/X Data (twscrape or snscrape)
**Why:** Twitter is a primary source for real-time trends and social sentiment.
```bash
pip install twscrape
```

### 2. Reddit Data (praw)
**Why:** Reddit provides community-driven discussions and emerging trends.
```bash
pip install praw
```

### 3. Better Article Extraction (trafilatura)
**Why:** More reliable than newspaper4k for content extraction.
```bash
pip install trafilatura
```

### 4. Keyword Extraction (keybert)
**Why:** Automatically extract keywords from news, transcripts, and trends.
```bash
pip install keybert
```

### 5. Production Monitoring (prometheus-fastapi-instrumentator)
**Why:** Essential for production deployments and SLA monitoring.
```bash
pip install prometheus-fastapi-instrumentator
```

---

## Implementation Notes

### Adding New Data Sources

When integrating new data sources, follow this pattern:

1. **Create a service class** in `app/services/`
2. **Create API router** in `app/api/{source_name}/`
3. **Add Pydantic models** for request/response validation
4. **Implement caching** using the existing cache_manager
5. **Add rate limiting** using the existing rate_limiter
6. **Write tests** in `tests/`

### Example Service Structure

```
app/
├── api/
│   └── reddit/
│       ├── __init__.py
│       └── reddit_api.py
├── services/
│   └── reddit_service.py
└── models/
    └── reddit_models.py
```

### Environment Variables

Add new credentials to `.env`:
```bash
# Reddit API
REDDIT_CLIENT_ID=your_client_id
REDDIT_CLIENT_SECRET=your_client_secret

# Twitter/X
TWITTER_USERNAME=your_username
TWITTER_PASSWORD=your_password

# LLM API Keys
OPENAI_API_KEY=your_openai_key
ANTHROPIC_API_KEY=your_anthropic_key
```

---

## Related Documentation

- [API Reference](../API_REFERENCE.md)
- [Architecture Overview](../ARCHITECTURE_OVERVIEW.md)
- [Performance Tuning](../PERFORMANCE_TUNING.md)
- [Security Guidelines](../SECURITY_GUIDELINES.md)
- [Roadmap](../ROADMAP.md)
