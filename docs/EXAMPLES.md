# API Usage Examples

This document provides comprehensive examples for using the Headwater API.

## Table of Contents

- [Authentication](#authentication)
- [Google News Examples](#google-news-examples)
- [Google Trends Examples](#google-trends-examples)
- [Google Autocomplete Examples](#google-autocomplete-examples)
- [YouTube Transcripts Examples](#youtube-transcripts-examples)
- [Python SDK Examples](#python-sdk-examples)
- [JavaScript/Node.js Examples](#javascriptnodejs-examples)
- [Error Handling Examples](#error-handling-examples)

## Authentication

All API requests require an API key in the `x-api-key` header:

```bash
export API_KEY="your_api_key_here"
```

## Google News Examples

### Basic News Search

```bash
curl -X GET "http://localhost:8000/api/v1/google-news/search?q=artificial+intelligence&country=US&language=en&max_results=5" \
  -H "x-api-key: $API_KEY"
```

**Response:**
```json
{
  "status": "success",
  "query": "artificial intelligence",
  "country": "US",
  "language": "en",
  "results": [
    {
      "title": "Latest Developments in AI Research",
      "link": "https://example.com/ai-research",
      "source": "Tech News",
      "published": "2025-09-14T08:00:00Z",
      "snippet": "Researchers have made significant progress...",
      "image_url": "https://example.com/image.jpg"
    }
  ],
  "total_results": 5,
  "metadata": {
    "request_id": "req_123456",
    "timestamp": "2025-09-14T10:30:00Z",
    "processing_time_ms": 150
  }
}
```

### Advanced News Search with Filters

```bash
curl -X GET "http://localhost:8000/api/v1/google-news/search?q=climate+change&country=US&language=en&max_results=10&sort_by=relevance&freshness=Day" \
  -H "x-api-key: $API_KEY"
```

### News Search with Date Range

```bash
curl -X GET "http://localhost:8000/api/v1/google-news/search?q=machine+learning&from_date=2025-09-01&to_date=2025-09-14" \
  -H "x-api-key: $API_KEY"
```

## Google Trends Examples

### Get Trending Topics

```bash
curl -X GET "http://localhost:8000/api/v1/google-trends/trending-now?geo=US&hours=24" \
  -H "x-api-key: $API_KEY"
```

**Response:**
```json
{
  "status": "success",
  "geo": "US",
  "hours": 24,
  "trending_topics": [
    {
      "title": "Breaking News Event",
      "search_volume": 1000000,
      "articles": [
        {
          "title": "Article Title",
          "url": "https://example.com/article",
          "source": "News Source"
        }
      ]
    }
  ],
  "metadata": {
    "request_id": "req_789012",
    "timestamp": "2025-09-14T10:30:00Z",
    "processing_time_ms": 200
  }
}
```

### Compare Multiple Keywords

```bash
curl -X GET "http://localhost:8000/api/v1/google-trends/interest-over-time?keywords=python,javascript,rust&geo=US&timeframe=1-Y" \
  -H "x-api-key: $API_KEY"
```

### Get Interest Over Time

```bash
curl -X GET "http://localhost:8000/api/v1/google-trends/interest-over-time?keywords=data+science,machine+learning&geo=US&timeframe=3-M" \
  -H "x-api-key: $API_KEY"
```

### Get Related Topics

```bash
curl -X GET "http://localhost:8000/api/v1/google-trends/related-queries?keywords=artificial+intelligence&geo=US" \
  -H "x-api-key: $API_KEY"
```

## Google Autocomplete Examples

### Basic Autocomplete

```bash
curl -X GET "http://localhost:8000/api/v1/google-autocomplete/autocomplete?q=python+programming&output=chrome&gl=US" \
  -H "x-api-key: $API_KEY"
```

**Response:**
```json
{
  "status": "success",
  "query": "python programming",
  "output": "chrome",
  "gl": "US",
  "suggestions": [
    "python programming tutorial",
    "python programming language",
    "python programming for beginners",
    "python programming examples",
    "python programming jobs"
  ],
  "metadata": {
    "request_id": "req_345678",
    "timestamp": "2025-09-14T10:30:00Z",
    "processing_time_ms": 100
  }
}
```

### Generate Keyword Variations

```bash
curl -X GET "http://localhost:8000/api/v1/google-autocomplete/autocomplete?q=seo+tools&variations=true&output=json&gl=US" \
  -H "x-api-key: $API_KEY"
```

### Autocomplete with Multiple Outputs

```bash
curl -X GET "http://localhost:8000/api/v1/google-autocomplete/autocomplete?q=marketing&output=firefox&gl=UK&hl=en" \
  -H "x-api-key: $API_KEY"
```

## YouTube Transcripts Examples

### Get Video Transcript

```bash
curl -X GET "http://localhost:8000/api/v1/youtube-transcripts/get-transcript?video_id=dQw4w9WgXcQ&language=en" \
  -H "x-api-key: $API_KEY"
```

**Response:**
```json
{
  "status": "success",
  "video_id": "dQw4w9WgXcQ",
  "language": "en",
  "transcript": [
    {
      "text": "Never gonna give you up",
      "start": 0.0,
      "duration": 3.5
    },
    {
      "text": "Never gonna let you down",
      "start": 3.5,
      "duration": 3.2
    }
  ],
  "metadata": {
    "request_id": "req_901234",
    "timestamp": "2025-09-14T10:30:00Z",
    "processing_time_ms": 250
  }
}
```

### Get Available Transcripts

```bash
curl -X GET "http://localhost:8000/api/v1/youtube-transcripts/list-transcripts?video_id=dQw4w9WgXcQ" \
  -H "x-api-key: $API_KEY"
```

**Response:**
```json
{
  "status": "success",
  "video_id": "dQw4w9WgXcQ",
  "available_languages": [
    {
      "language": "en",
      "language_name": "English",
      "is_generated": false
    },
    {
      "language": "es",
      "language_name": "Spanish",
      "is_generated": true
    }
  ],
  "metadata": {
    "request_id": "req_567890",
    "timestamp": "2025-09-14T10:30:00Z",
    "processing_time_ms": 150
  }
}
```

## Python SDK Examples

### Basic Usage

```python
import asyncio
from headwater import HeadwaterClient


async def main():
    api_key = "your_api_key_here"
    client = HeadwaterClient(api_key)

    # Get news
    news = await client.google_news.search("artificial intelligence")
    print(f"Found {len(news.results)} articles")

    # Get autocomplete suggestions
    suggestions = await client.google_autocomplete.get_suggestions("python programming")
    print(f"Suggestions: {suggestions}")

    # Get trending topics
    trends = await client.google_trends.get_trending()
    print(f"Trending: {trends[0].title if trends else 'None'}")


asyncio.run(main())
```

### Advanced Python Example

```python
import asyncio
import json
from headwater import HeadwaterClient


async def comprehensive_example():
    client = HeadwaterClient("your_api_key_here")

    try:
        # Get news with filters
        news_response = await client.google_news.search(
            query="machine learning", country="US", language="en", max_results=10, sort_by="relevance", freshness="Week"
        )

        print(f"News search completed: {len(news_response.results)} results")

        # Get autocomplete with variations
        autocomplete_response = await client.google_autocomplete.get_suggestions(
            query="data science", variations=True, output="json", geo="US"
        )

        print(f"Autocomplete suggestions: {len(autocomplete_response.suggestions)}")

        # Get trends comparison
        trends_response = await client.google_trends.compare_keywords(
            keywords=["python", "javascript", "rust"], geo="US", timeframe="1-Y"
        )

        print(f"Trends comparison completed for {len(trends_response.keywords)} keywords")

        # Get YouTube transcript
        transcript_response = await client.youtube_transcripts.get_transcript(video_id="dQw4w9WgXcQ", language="en")

        print(f"Transcript retrieved: {len(transcript_response.transcript)} segments")

    except Exception as e:
        print(f"Error: {e}")


asyncio.run(comprehensive_example())
```

### Error Handling in Python

```python
import asyncio
from headwater import HeadwaterClient, HeadwaterError


async def robust_example():
    client = HeadwaterClient("your_api_key_here")

    try:
        # This might fail if the API key is invalid
        response = await client.google_news.search("test query")
        print(f"Success: {response.status}")

    except HeadwaterError as e:
        if e.status_code == 401:
            print("API key is invalid or expired")
        elif e.status_code == 429:
            print("Rate limit exceeded, try again later")
        else:
            print(f"API error: {e.message}")

    except Exception as e:
        print(f"Unexpected error: {e}")


asyncio.run(robust_example())
```

## JavaScript/Node.js Examples

### Basic Usage with Axios

```javascript
const axios = require('axios');

const apiKey = 'your_api_key_here';
const baseURL = 'http://localhost:8000/api/v1';

const headers = { 'x-api-key': apiKey };

// Get news
axios.get(`${baseURL}/google-news/search`, {
  params: { q: 'artificial intelligence', country: 'US', max_results: 5 },
  headers: headers
})
.then(response => {
  console.log('News:', response.data.results.length, 'articles found');
})
.catch(error => {
  console.error('Error:', error.response.data);
});

// Get autocomplete
axios.get(`${baseURL}/google-autocomplete/autocomplete`, {
  params: { q: 'python programming', variations: true },
  headers: headers
})
.then(response => {
  console.log('Autocomplete:', response.data.suggestions);
})
.catch(error => {
  console.error('Error:', error.response.data);
});
```

### Advanced JavaScript Example

```javascript
const axios = require('axios');

class HeadwaterAPI {
  constructor(apiKey) {
    this.apiKey = apiKey;
    this.baseURL = 'http://localhost:8000/api/v1';
    this.client = axios.create({
      headers: { 'x-api-key': apiKey },
      timeout: 30000
    });
  }

  async getNews(query, options = {}) {
    try {
      const response = await this.client.get(`${this.baseURL}/google-news/search`, {
        params: { q: query, ...options }
      });
      return response.data;
    } catch (error) {
      throw new Error(`News search failed: ${error.message}`);
    }
  }

  async getAutocomplete(query, options = {}) {
    try {
      const response = await this.client.get(`${this.baseURL}/google-autocomplete/autocomplete`, {
        params: { q: query, ...options }
      });
      return response.data;
    } catch (error) {
      throw new Error(`Autocomplete failed: ${error.message}`);
    }
  }

  async getTrends(keywords, options = {}) {
    try {
      const response = await this.client.get(`${this.baseURL}/google-trends/interest-over-time`, {
        params: { keywords: keywords.join(','), ...options }
      });
      return response.data;
    } catch (error) {
      throw new Error(`Trends failed: ${error.message}`);
    }
  }
}

// Usage
const api = new HeadwaterAPI('your_api_key_here');

async function example() {
  try {
    const news = await api.getNews('machine learning', { country: 'US', max_results: 10 });
    console.log(`Found ${news.results.length} news articles`);

    const suggestions = await api.getAutocomplete('data science', { variations: true });
    console.log(`Found ${suggestions.suggestions.length} suggestions`);

    const trends = await api.getTrends(['python', 'javascript'], { geo: 'US', timeframe: '1-Y' });
    console.log('Trends comparison completed');

  } catch (error) {
    console.error('API call failed:', error.message);
  }
}

example();
```

### JavaScript with Fetch API

```javascript
const API_KEY = 'your_api_key_here';
const BASE_URL = 'http://localhost:8000/api/v1';

async function apiCall(endpoint, params = {}) {
  const url = new URL(`${BASE_URL}${endpoint}`);
  Object.keys(params).forEach(key => url.searchParams.append(key, params[key]));

  const response = await fetch(url, {
    headers: {
      'x-api-key': API_KEY,
      'Content-Type': 'application/json'
    }
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(`API Error: ${error.title} - ${error.detail}`);
  }

  return response.json();
}

// Usage examples
async function examples() {
  try {
    // Get news
    const news = await apiCall('/google-news/search', {
      q: 'artificial intelligence',
      country: 'US',
      max_results: 5
    });
    console.log('News results:', news.results.length);

    // Get autocomplete
    const autocomplete = await apiCall('/google-autocomplete/autocomplete', {
      q: 'python programming',
      variations: true
    });
    console.log('Autocomplete suggestions:', autocomplete.suggestions);

    // Get trends
    const trends = await apiCall('/google-trends/trending', {
      geo: 'US',
      hours: 24
    });
    console.log('Trending topics:', trends.trending_topics.length);

  } catch (error) {
    console.error('Error:', error.message);
  }
}

examples();
```

## Error Handling Examples

### HTTP Status Code Handling

```python
import httpx
import asyncio


async def handle_errors():
    api_key = "your_api_key_here"
    headers = {"x-api-key": api_key}

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                "http://localhost:8000/api/v1/google-news/search", params={"q": "test query"}, headers=headers
            )
            response.raise_for_status()
            data = response.json()
            print(f"Success: {data['status']}")

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                print("❌ Authentication failed: Check your API key")
            elif e.response.status_code == 429:
                print("⏰ Rate limit exceeded: Wait before retrying")
            elif e.response.status_code == 400:
                print("📝 Bad request: Check your parameters")
            else:
                print(f"🔥 HTTP error {e.response.status_code}: {e.response.text}")

        except httpx.RequestError as e:
            print(f"🌐 Network error: {e}")

        except Exception as e:
            print(f"💥 Unexpected error: {e}")


asyncio.run(handle_errors())
```

### RFC7807 Error Response Format

```javascript
// Example error response
{
  "type": "https://headwater.com/problems/rate_limit_exceeded",
  "title": "Too Many Requests",
  "status": 429,
  "detail": "Rate limit exceeded. Try again in 60 seconds.",
  "instance": "/api/v1/google-news/search"
}

// Error handling in JavaScript
function handleApiError(error) {
  if (error.response) {
    const { status, data } = error.response;

    switch (status) {
      case 401:
        console.error('🔐 Authentication Error:', data.detail);
        // Redirect to login or refresh token
        break;

      case 429:
        console.error('⏱️ Rate Limited:', data.detail);
        // Implement backoff strategy
        break;

      case 400:
        console.error('📝 Bad Request:', data.detail);
        // Show validation errors to user
        break;

      case 500:
        console.error('🔥 Server Error:', data.detail);
        // Show generic error message
        break;

      default:
        console.error('❓ Unknown Error:', data.title);
    }
  } else {
    console.error('🌐 Network Error:', error.message);
  }
}
```

### Retry Logic Example

```python
import asyncio
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10),
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.RequestError)),
)
async def robust_api_call(endpoint, params=None, headers=None):
    async with httpx.AsyncClient() as client:
        response = await client.get(f"http://localhost:8000/api/v1{endpoint}", params=params, headers=headers)
        response.raise_for_status()
        return response.json()


async def example_with_retry():
    headers = {"x-api-key": "your_api_key_here"}

    try:
        # This will retry automatically on failures
        news = await robust_api_call("/google-news/search", params={"q": "artificial intelligence"}, headers=headers)
        print(f"Success after retries: {len(news['results'])} results")

    except Exception as e:
        print(f"Failed after all retries: {e}")


asyncio.run(example_with_retry())
```

## Advanced Examples

### Batch Processing

```python
import asyncio
import httpx
from typing import List, Dict


async def batch_news_search(queries: List[str], api_key: str) -> List[Dict]:
    """Search for multiple queries concurrently"""
    headers = {"x-api-key": api_key}

    async def search_single(query: str):
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "http://localhost:8000/api/v1/google-news/search",
                params={"q": query, "max_results": 5},
                headers=headers,
            )
            response.raise_for_status()
            return response.json()

    # Execute all searches concurrently
    tasks = [search_single(query) for query in queries]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Handle results and exceptions
    processed_results = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            print(f"Query '{queries[i]}' failed: {result}")
            processed_results.append({"query": queries[i], "error": str(result)})
        else:
            processed_results.append(result)

    return processed_results


async def main():
    queries = ["artificial intelligence", "machine learning", "data science", "python programming"]

    results = await batch_news_search(queries, "your_api_key_here")

    for result in results:
        if "error" in result:
            print(f"❌ {result['query']}: {result['error']}")
        else:
            print(f"✅ {result['query']}: {len(result['results'])} articles")


asyncio.run(main())
```

### Streaming Responses (if supported)

```python
import asyncio
import httpx
import json


async def stream_large_response():
    """Handle large responses efficiently"""
    api_key = "your_api_key_here"
    headers = {"x-api-key": api_key}

    async with httpx.AsyncClient() as client:
        async with client.stream(
            "GET",
            "http://localhost:8000/api/v1/google-news/search",
            params={"q": "big data", "max_results": 100},
            headers=headers,
        ) as response:
            response.raise_for_status()

            # Process response in chunks
            async for chunk in response.aiter_text():
                if chunk:
                    # Parse JSON chunks as they arrive
                    try:
                        data = json.loads(chunk)
                        print(f"Received {len(data.get('results', []))} articles")
                    except json.JSONDecodeError:
                        # Handle partial JSON
                        pass


asyncio.run(stream_large_response())
```

---

These examples demonstrate the full range of Headwater API capabilities. For more detailed information, see the [API Reference](API_REFERENCE.md) and [Troubleshooting Guide](TROUBLESHOOTING.md).
