# API Structure

This document provides an overview of the Headwater API structure, organization, and versioning strategy.

## Directory Structure

The API is organized into versioned modules under the `app/api/` directory:

```
app/
├── api/
│   ├── v1/
│   │   ├── google_autocomplete/
│   │   │   └── google_autocomplete_api.py
│   │   ├── google_news/
│   │   │   └── google_news_api.py
│   │   ├── google_trends/
│   │   │   └── google_trends_api.py
│   │   └── youtube_transcripts/
│   │       └── youtube_transcripts_api.py
│   └── __init__.py
├── core/
│   ├── auth.py
│   ├── base_router.py
│   ├── cache_manager.py
│   ├── config.py
│   ├── database.py
│   ├── dependencies.py
│   ├── exceptions.py
│   ├── health_checks.py
│   ├── middleware.py
│   ├── proxy.py
│   ├── rate_limiter.py
│   ├── security.py
│   └── utils.py
├── models/
└── utils/
```

## API Versioning

All endpoints are versioned using URL path versioning with the format `/api/v1/...`. This approach ensures:

1. **Backward Compatibility**: New versions can be introduced without breaking existing clients
2. **Clear Evolution Path**: Clients can easily understand which version they're using
3. **Parallel Development**: Multiple API versions can be maintained simultaneously

### Version Lifecycle

- **v1**: Current stable version
- **v0**: Legacy version (deprecated)
- **v2**: Future version (in planning)

## BaseRouter

The `BaseRouter` class in `app/core/base_router.py` provides a foundation for all API routers with consistent behavior:

```python
class BaseRouter(APIRouter):
    def __init__(
        self,
        prefix: str,
        service_name: Optional[str] = None,
        responses: Optional[Dict[int, dict]] = None,
        **kwargs
    ):
        # Extract service_name from prefix if not provided
        if service_name is None:
            parts = prefix.strip("/").split("/")
            service_name = parts[1] if len(parts) > 1 else parts[0]
        
        # Validate consistency between extracted service_name and any explicitly passed name
        if service_name and prefix and service_name not in prefix:
            raise ValueError(f"Service name '{service_name}' must be part of prefix '{prefix}'")
        
        # Initialize with standard parameters
        super().__init__(
            prefix=prefix,
            tags=[service_name],
            responses=responses,
            dependencies=[Depends(authenticate_api_key)],
            **kwargs
        )
```

### Usage Example

```python
from app.core.base_router import BaseRouter

router = BaseRouter(prefix="/api/v1/google-news")

@router.get("/search")
async def search_news(query: str):
    # Implementation
    pass
```

## URL Structure

The API follows a consistent URL structure:

- `/api/v1/{service}/{resource}/{action}`

Examples:
- `/api/v1/google-news/search`
- `/api/v1/google-trends/interest-over-time`
- `/api/v1/google-autocomplete/autocomplete`
- `/api/v1/youtube-transcripts/get`

## Error Handling

All API endpoints use a standardized error response format following RFC7807 Problem Details:

```json
{
  "type": "https://headwater.com/problems/validation_error",
  "title": "Bad Request",
  "status": 400,
  "detail": "Invalid parameter: query cannot be empty"
}
```

## Authentication

All API endpoints require authentication using an API key provided in the `x-api-key` header:

```bash
curl -X GET "http://localhost:8000/api/v1/google-news/search?q=ai" \
  -H "x-api-key: your_api_key"
```

## Rate Limiting

Rate limiting is applied to all endpoints based on the client's API key or IP address. Default limits are:

- 100 requests per hour per API key
- 10 requests per minute per IP address (for unauthenticated requests)

## Documentation

API documentation is available at:

- Swagger UI: `/api/docs`
- ReDoc: `/api/redoc`
- OpenAPI Schema: `/api/openapi.json`
