# BaseRouter

The `BaseRouter` class provides a standardized way to create API routers in the Headwater application. It extends FastAPI's `APIRouter` with additional features for service name extraction, RFC7807 compliant error responses, and OpenAPI documentation.

## Features

- **Auto-derives service_name from prefix** - If not provided, the service name is extracted from the URL prefix
- **Supports OpenAPI responses documentation** - Provides standardized response schemas for common HTTP status codes
- **Provides RFC7807 compliant error responses** - All error responses follow the RFC7807 JSON structure
- **Centralizes authentication** - All routes automatically include API key authentication

## Usage

### Basic Usage

```python
from app.core.base_router import BaseRouter

# Create a router with auto-derived service name
router = BaseRouter(prefix="/google-trends")


# Define routes
@router.get("/related-queries")
async def related_queries(keyword: str):
    return {"keyword": keyword, "rising": [], "top": []}


# Include the router in your FastAPI app
app.include_router(router())
```

### With Explicit Service Name

```python
# Create a router with explicit service name
router = BaseRouter(prefix="/google-trends", service_name="google-trends-service")
```

### With Custom Responses

```python
# Define custom responses for OpenAPI documentation
custom_responses = {
    200: {
        "description": "Success",
        "content": {"application/json": {"example": {"keyword": "self hosting", "rising": [], "top": []}}},
    },
    400: {
        "description": "Bad Request",
        "content": {
            "application/problem+json": {
                "example": {
                    "type": "https://headwater.com/problems/validation_error",
                    "title": "Validation Error",
                    "status": 400,
                    "detail": "Invalid parameters",
                    "fields": ["query"],
                }
            }
        },
    },
}

# Create a router with custom responses
router = BaseRouter(prefix="/google-trends", responses=custom_responses)
```

## Error Handling

The `BaseRouter` class provides several methods for raising RFC7807 compliant error responses:

### raise_http_exception

```python
@router.get("/{item_id}")
async def get_item(item_id: str):
    if not item_exists(item_id):
        router.raise_http_exception(
            status_code=404, detail=f"Item with ID {item_id} not found", type="item_not_found", item_id=item_id
        )
    return get_item_by_id(item_id)
```

### Convenience Methods

```python
# Raise a 400 Bad Request error
router.raise_validation_error("Invalid email format", field="email")

# Raise a 404 Not Found error
router.raise_not_found_error("User", user_id)

# Raise a 500 Internal Server Error
router.raise_internal_error("Database connection failed")
```

## Service Name Extraction

The `BaseRouter` class automatically extracts the service name from the URL prefix:

- `/google-trends` → `google-trends`
- `/youtube-transcripts` → `youtube-transcripts`
- `/api/v1/google-news` → `google-news`

If a service name is explicitly provided, it will be used instead of the extracted one. If the provided service name differs from the extracted one, a warning will be logged.

## RFC7807 Error Responses

All error responses follow the RFC7807 JSON structure:

```json
{
  "type": "https://headwater.com/problems/validation_error",
  "title": "Validation Error",
  "status": 400,
  "detail": "Invalid email format",
  "field": "email"
}
```

The `type` field is automatically converted to a URI if a simple string is provided:

- `validation_error` → `https://headwater.com/problems/validation_error`
- `https://example.com/errors/server_error` → (unchanged)

## Additional Fields

Additional fields can be included in error responses by passing them as keyword arguments:

```python
router.raise_http_exception(
    status_code=400,
    detail="Invalid parameters",
    type="validation_error",
    field="email",
    code="invalid_format",
    suggestion="Use a valid email address format",
)
```

This will include `field`, `code`, and `suggestion` fields in the error response.
