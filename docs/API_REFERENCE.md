# API Reference

This document provides comprehensive documentation for all Headwater API endpoints, including request/response formats, parameters, authentication requirements, and usage examples.

## Table of Contents

- [Authentication](#authentication)
- [Google News API](#google-news-api)
- [Google Autocomplete API](#google-autocomplete-api)
- [Google Trends API](#google-trends-api)
- [YouTube Transcripts API](#youtube-transcripts-api)
- [Error Codes](#error-codes)
- [Rate Limiting](#rate-limiting)

## Authentication

All API endpoints require authentication using an API key. Include your API key in the request headers:

```bash
X-API-Key: YOUR_API_KEY
```

### Obtaining an API Key

API keys are provided upon registration. Contact the Headwater team to get your API key.

## Google News API

The Google News API provides access to news articles from various sources with comprehensive filtering and search capabilities.

### Base URL

```bash
https://api.headwater.com/api/v1/google-news/
```

### Endpoints

#### 1. Search News Articles

**Endpoint:** `GET /search`

Search for news articles based on keywords, with support for advanced filtering options.

**Parameters:**

- `q` (string, required): Search query keywords
- `lang` (string, optional): Language code (default: "en")
- `country` (string, optional): Country code (default: "US")
- `max` (integer, optional): Maximum number of results (default: 10, max: 100)
- `from_date` (string, optional): Start date in YYYY-MM-DD format
- `to_date` (string, optional): End date in YYYY-MM-DD format
- `sort_by` (string, optional): Sort order - "relevance", "date", "rank" (default: "relevance")
- `page` (integer, optional): Page number for pagination (default: 1)
- `topic` (string, optional): News topic filter
- `source` (string, optional): News source filter

**Example Request:**

```bash
curl -X GET "https://api.headwater.com/api/v1/google-news/search?q=artificial+intelligence&max=20&sort_by=date" \
  -H "X-API-Key: YOUR_API_KEY"
```

**Response:**

```json
{
  "articles": [
    {
      "title": "AI Breakthrough in Medical Research",
      "description": "New AI technology shows promising results...",
      "url": "https://example.com/ai-breakthrough",
      "source": "Tech News Daily",
      "publishedAt": "2024-01-15T10:30:00Z",
      "image_url": "https://example.com/image.jpg"
    }
  ],
  "total_results": 1,
  "page": 1,
  "status": "success"
}
```

#### 2. Get Top News

**Endpoint:** `GET /top`

Retrieve top news headlines from various categories.

**Parameters:**

- `category` (string, optional): News category - "general", "business", "technology", "sports", "entertainment", "health", "science"
- `lang` (string, optional): Language code (default: "en")
- `country` (string, optional): Country code (default: "US")
- `max` (integer, optional): Maximum number of results (default: 10, max: 100)

**Example Request:**

```bash
curl -X GET "https://api.headwater.com/api/v1/google-news/top?category=technology&max=15" \
  -H "X-API-Key: YOUR_API_KEY"
```

#### 3. Get News by Topic

**Endpoint:** `GET /topic`

Retrieve news articles filtered by specific topics.

**Parameters:**

- `topic` (string, required): Topic identifier
- `lang` (string, optional): Language code (default: "en")
- `max` (integer, optional): Maximum number of results (default: 10, max: 100)

**Example Request:**

```bash
curl -X GET "https://api.headwater.com/api/v1/google-news/topic?topic=technology&max=20" \
  -H "X-API-Key: YOUR_API_KEY"
```

#### 4. Get News by Location

**Endpoint:** `GET /location`

Retrieve news articles from specific geographic locations.

**Parameters:**

- `location` (string, required): Location identifier
- `lang` (string, optional): Language code (default: "en")
- `max` (integer, optional): Maximum number of results (default: 10, max: 100)

**Example Request:**

```bash
curl -X GET "https://api.headwater.com/api/v1/google-news/location?location=united-states&max=15" \
  -H "X-API-Key: YOUR_API_KEY"
```

#### 5. Get News by Source

**Endpoint:** `GET /source`

Retrieve news articles from specific news sources.

**Parameters:**

- `source` (string, required): News source identifier
- `lang` (string, optional): Language code (default: "en")
- `max` (integer, optional): Maximum number of results (default: 10, max: 100)

**Example Request:**

```bash
curl -X GET "https://api.headwater.com/api/v1/google-news/source?source=cnn&max=20" \
  -H "X-API-Key: YOUR_API_KEY"
```

#### 6. Get Article Details

**Endpoint:** `GET /article-details`

Retrieve detailed information about a specific news article.

**Parameters:**

- `url` (string, required): Article URL
- `lang` (string, optional): Language code (default: "en")

**Example Request:**

```bash
curl -X GET "https://api.headwater.com/api/v1/google-news/article-details?url=https://example.com/article" \
  -H "X-API-Key: YOUR_API_KEY"
```

## Google Autocomplete API

The Google Autocomplete API provides access to Google's autocomplete suggestions with comprehensive parameter support.

### Base URL

```bash
https://api.headwater.com/api/v1/google-autocomplete/
```

### Endpoints

#### 1. Get Autocomplete Suggestions

**Endpoint:** `GET /autocomplete`

Get Google autocomplete suggestions with support for all available parameters.

**Parameters:**

- `q` (string, required): Search query string
- `output` (string, optional): Response format - "toolbar", "chrome", "firefox", "xml", "safari", "opera" (default: "toolbar")
- `client` (string, optional): Client identifier - "firefox", "chrome", "safari", "opera"
- `gl` (string, optional): Geographic location (ISO country code, default: "US")
- `hl` (string, optional): Host language (ISO language code, default: "en")
- `cr` (string, optional): Country restrict (e.g., "countryUS")
- `ds` (string, optional): Data source - "", "yt", "i", "n", "s", "v", "b", "p", "fin", "recipe", "scholar", "play", "maps", "flights", "hotels"
- `spell` (integer, optional): Enable spell correction (0=disabled, 1=enabled, default: 1)
- `cp` (integer, optional): Cursor position in query
- `variations` (boolean, optional): Generate keyword variations (default: false)

**Example Request:**

```bash
curl -X GET "https://api.headwater.com/api/v1/google-autocomplete/autocomplete?q=python+tutorial&output=chrome&variations=true" \
  -H "X-API-Key: YOUR_API_KEY"
```

**Response (Standard Mode):**

```json
{
  "response_type": "json",
  "original_query": "python tutorial",
  "suggestions": ["python tutorial", "python tutorial for beginners", "python tutorial pdf"],
  "descriptions": [],
  "query_completions": [],
  "metadata": {
    "google:clientdata": {"bpc": true, "tlw": false},
    "google:suggesttype": ["QUERY", "QUERY", "QUERY"],
    "google:verbatimrelevance": 1300
  }
}
```

**Response (Variations Mode):**

```json
{
  "success": true,
  "message": "Success! Keywords Generated",
  "keyword_data": {
    "suggestions": {
      "Questions": {
        "what": ["what is python tutorial", "what python tutorial to start with"],
        "how": ["how to python tutorial", "how python tutorial works"]
      },
      "Intent-Based": {
        "buy": ["buy python tutorial", "buy python tutorial book"],
        "learn": ["learn python tutorial", "learn python tutorial online"]
      }
    },
    "metadata": {
      "Questions:what": {
        "query": "what python tutorial",
        "metadata": {"google:clientdata": {"bpc": true, "tlw": false}}
      }
    }
  }
}
```

## Google Trends API

The Google Trends API provides access to Google Trends data including interest over time, regional interest, and trending topics.

### Base URL

```bash
https://api.headwater.com/api/v1/google-trends/
```

### Endpoints

#### 1. Interest Over Time

**Endpoint:** `GET /interest-over-time`

Get interest over time data for specified keywords.

**Parameters:**

- `keywords` (string, required): Comma-separated keywords
- `timeframe` (string, optional): Time period (default: "today 12-m")
- `geo` (string, optional): Geographic location code
- `cat` (string, optional): Category ID
- `gprop` (string, optional): Google property

**Example Request:**

```bash
curl -X GET "https://api.headwater.com/api/v1/google-trends/interest-over-time?keywords=python,javascript&timeframe=today+3-m" \
  -H "X-API-Key: YOUR_API_KEY"
```

**Response:**

```json
{
  "data": [
    {
      "date": "2024-01-01",
      "python": 85,
      "javascript": 92,
      "isPartial": false
    }
  ]
}
```

#### 2. Interest by Region

**Endpoint:** `GET /interest-by-region`

Get regional interest data for a keyword.

**Parameters:**

- `keyword` (string, required): Single keyword
- `timeframe` (string, optional): Time period (default: "today 12-m")
- `geo` (string, optional): Geographic location code
- `cat` (string, optional): Category ID
- `resolution` (string, optional): Resolution level - "COUNTRY", "REGION", "CITY" (default: "COUNTRY")

**Example Request:**

```bash
curl -X GET "https://api.headwater.com/api/v1/google-trends/interest-by-region?keyword=python&resolution=COUNTRY" \
  -H "X-API-Key: YOUR_API_KEY"
```

#### 3. Related Queries

**Endpoint:** `GET /related-queries`

Get queries related to a keyword.

**Parameters:**

- `keyword` (string, required): Single keyword
- `timeframe` (string, optional): Time period (default: "today 12-m")
- `geo` (string, optional): Geographic location code
- `cat` (string, optional): Category ID
- `gprop` (string, optional): Google property

**Example Request:**

```bash
curl -X GET "https://api.headwater.com/api/v1/google-trends/related-queries?keyword=python" \
  -H "X-API-Key: YOUR_API_KEY"
```

#### 4. Related Topics

**Endpoint:** `GET /related-topics`

Get topics related to a keyword.

**Parameters:**

- `keyword` (string, required): Single keyword
- `timeframe` (string, optional): Time period (default: "today 12-m")
- `geo` (string, optional): Geographic location code
- `cat` (string, optional): Category ID
- `gprop` (string, optional): Google property

**Example Request:**

```bash
curl -X GET "https://api.headwater.com/api/v1/google-trends/related-topics?keyword=python" \
  -H "X-API-Key: YOUR_API_KEY"
```

#### 5. Trending Now

**Endpoint:** `GET /trending-now`

Get currently trending searches.

**Parameters:**

- `geo` (string, optional): Geographic location code (default: "US")

**Example Request:**

```bash
curl -X GET "https://api.headwater.com/api/v1/google-trends/trending-now?geo=US" \
  -H "X-API-Key: YOUR_API_KEY"
```

#### 6. Trending Now by RSS

**Endpoint:** `GET /trending-now-by-rss`

Get trending searches with related news articles.

**Parameters:**

- `geo` (string, optional): Geographic location code (default: "US")

**Example Request:**

```bash
curl -X GET "https://api.headwater.com/api/v1/google-trends/trending-now-by-rss?geo=US" \
  -H "X-API-Key: YOUR_API_KEY"
```

#### 7. Related News by IDs

**Endpoint:** `GET /trending-now-news-by-ids`

Get news articles related to trending topics.

**Parameters:**

- `news_tokens` (string, required): Comma-separated news tokens
- `max_news` (integer, optional): Maximum number of articles (default: 3)

**Example Request:**

```bash
curl -X GET "https://api.headwater.com/api/v1/google-trends/trending-now-news-by-ids?news_tokens=token1,token2&max_news=5" \
  -H "X-API-Key: YOUR_API_KEY"
```

#### 8. Trending Timeline

**Endpoint:** `GET /trending-now-showcase-timeline`

Get trending timeline data for keywords.

**Parameters:**

- `keywords` (string, required): Comma-separated keywords
- `timeframe` (string, required): Timeframe - "past_4h", "past_24h", "past_48h", "past_7d"

**Example Request:**

```bash
curl -X GET "https://api.headwater.com/api/v1/google-trends/trending-now-showcase-timeline?keywords=python&timeframe=past_24h" \
  -H "X-API-Key: YOUR_API_KEY"
```

#### 9. Categories

**Endpoint:** `GET /categories`

Search or list Google Trends categories.

**Parameters:**

- `find` (string, optional): String to match category name
- `root` (string, optional): Root category ID

**Example Request:**

```bash
curl -X GET "https://api.headwater.com/api/v1/google-trends/categories?find=technology" \
  -H "X-API-Key: YOUR_API_KEY"
```

#### 10. Geolocation Codes

**Endpoint:** `GET /geo`

Search available geolocation codes.

**Parameters:**

- `find` (string, optional): String to match location name

**Example Request:**

```bash
curl -X GET "https://api.headwater.com/api/v1/google-trends/geo?find=united" \
  -H "X-API-Key: YOUR_API_KEY"
```

## YouTube Transcripts API

The YouTube Transcripts API provides access to video transcripts with support for multiple languages and formats.

### Base URL

```bash
https://api.headwater.com/api/v1/youtube-transcripts/
```

### Endpoints

#### 1. Get Transcript

**Endpoint:** `GET /get-transcript`

Get transcript for a YouTube video.

**Parameters:**

- `video_id` (string, required): YouTube video ID
- `languages` (array, optional): List of language codes (default: ["en"])
- `preserve_formatting` (boolean, optional): Preserve HTML formatting (default: false)

**Example Request:**

```bash
curl -X GET "https://api.headwater.com/api/v1/youtube-transcripts/get-transcript?video_id=dQw4w9WgXcQ&languages=en" \
  -H "X-API-Key: YOUR_API_KEY"
```

**Response:**

```json
{
  "video_id": "dQw4w9WgXcQ",
  "language": "English",
  "language_code": "en",
  "is_generated": false,
  "is_translatable": true,
  "translation_languages": [
    {"language": "Spanish", "language_code": "es"},
    {"language": "French", "language_code": "fr"}
  ],
  "transcript": [
    {
      "text": "Never gonna give you up",
      "start": 0.0,
      "duration": 3.5
    }
  ]
}
```

#### 2. List Available Transcripts

**Endpoint:** `GET /list-transcripts`

List all available transcripts for a video.

**Parameters:**

- `video_id` (string, required): YouTube video ID

**Example Request:**

```bash
curl -X GET "https://api.headwater.com/api/v1/youtube-transcripts/list-transcripts?video_id=dQw4w9WgXcQ" \
  -H "X-API-Key: YOUR_API_KEY"
```

**Response:**

```json
{
  "transcripts": [
    {
      "video_id": "dQw4w9WgXcQ",
      "language": "English",
      "language_code": "en",
      "is_generated": false,
      "is_translatable": true,
      "translation_languages": [
        {"language": "Spanish", "language_code": "es"}
      ]
    }
  ]
}
```

#### 3. Translate Transcript

**Endpoint:** `GET /translate-transcript`

Translate a transcript to another language.

**Parameters:**

- `video_id` (string, required): YouTube video ID
- `target_language` (string, required): Target language code
- `source_languages` (array, optional): Source language codes (default: ["en"])

**Example Request:**

```bash
curl -X GET "https://api.headwater.com/api/v1/youtube-transcripts/translate-transcript?video_id=dQw4w9WgXcQ&target_language=es" \
  -H "X-API-Key: YOUR_API_KEY"
```

#### 4. Batch Get Transcripts

**Endpoint:** `POST /batch-get-transcripts`

Get transcripts for multiple videos.

**Parameters:**

- `video_ids` (array, required): List of YouTube video IDs
- `languages` (array, optional): List of language codes (default: ["en"])
- `preserve_formatting` (boolean, optional): Preserve HTML formatting (default: false)

**Example Request:**

```bash
curl -X POST "https://api.headwater.com/api/v1/youtube-transcripts/batch-get-transcripts?video_ids=dQw4w9WgXcQ,video2&languages=en" \
  -H "X-API-Key: YOUR_API_KEY"
```

#### 5. Format Transcript

**Endpoint:** `GET /format-transcript`

Get transcript in different formats.

**Parameters:**

- `video_id` (string, required): YouTube video ID
- `format_type` (string, required): Format type - "json", "txt", "vtt", "srt", "csv"
- `languages` (array, optional): List of language codes (default: ["en"])

**Example Request:**

```bash
curl -X GET "https://api.headwater.com/api/v1/youtube-transcripts/format-transcript?video_id=dQw4w9WgXcQ&format_type=srt" \
  -H "X-API-Key: YOUR_API_KEY"
```

**Response (SRT format):**

```json
{
  "formatted_transcript": "1\\n00:00:00,000 --> 00:00:03,500\\nNever gonna give you up\\n\\n2\\n00:00:03,500 --> 00:00:07,000\\nNever gonna let you down"
}
```

## Error Codes

The API uses standard HTTP status codes and provides detailed error messages:

### Common HTTP Status Codes

- **200 OK**: Request successful
- **400 Bad Request**: Invalid request parameters
- **401 Unauthorized**: Invalid or missing API key
- **403 Forbidden**: Access denied (transcripts disabled, etc.)
- **404 Not Found**: Resource not found
- **429 Too Many Requests**: Rate limit exceeded
- **500 Internal Server Error**: Server error

### Error Response Format

```json
{
  "error": {
    "code": "INVALID_PARAMETER",
    "message": "The 'q' parameter is required",
    "details": {
      "parameter": "q",
      "provided_value": null
    }
  }
}
```

### Common Error Codes

- `INVALID_PARAMETER`: Invalid or missing parameter
- `MISSING_API_KEY`: API key not provided
- `INVALID_API_KEY`: Invalid API key format
- `RATE_LIMIT_EXCEEDED`: Too many requests
- `SERVICE_UNAVAILABLE`: External service unavailable
- `QUOTA_EXCEEDED`: API quota exceeded

## Rate Limiting

The API implements rate limiting to ensure fair usage:

### Limits

- **Free Tier**: 100 requests per hour
- **Basic Tier**: 1,000 requests per hour
- **Pro Tier**: 10,000 requests per hour
- **Enterprise Tier**: Custom limits

### Rate Limit Headers

Rate limit information is included in response headers:

```bash
X-RateLimit-Limit: 1000
X-RateLimit-Remaining: 999
X-RateLimit-Reset: 1640995200
X-RateLimit-Retry-After: 3600
```

### Handling Rate Limits

When rate limited, the API returns HTTP status 429 with a `Retry-After` header indicating when to retry:

```json
{
  "error": {
    "code": "RATE_LIMIT_EXCEEDED",
    "message": "Rate limit exceeded. Try again in 3600 seconds.",
    "retry_after": 3600
  }
}
```

### Best Practices

1. Implement exponential backoff for retries
2. Monitor rate limit headers
3. Cache responses when possible
4. Use batch endpoints for multiple requests
5. Upgrade your plan for higher limits

## Support

For additional support or questions about the API:

- **Documentation**: <https://docs.headwater.com>
- **API Status**: <https://status.headwater.com>
- **Support Email**: support@headwater.com
- **Community Forum**: <https://community.headwater.com>
