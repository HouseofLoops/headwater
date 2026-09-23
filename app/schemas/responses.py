"""
Central response model definitions for the Headwater API.

This module provides base response models that can be extended
by specific API endpoints for consistent response structure.
"""

from typing import Any

from pydantic import BaseModel, Field


class BaseAPIResponse(BaseModel):
    """
    Base response model for all API endpoints.

    Provides a consistent structure for API responses including
    status, message, and optional metadata.
    """

    success: bool = Field(True, description="Whether the request was successful")
    message: str | None = Field(None, description="Optional status message")
    data: Any | None = Field(None, description="Response payload")


class ErrorResponse(BaseModel):
    """
    Standard error response model.

    Used for consistent error handling across all endpoints.
    """

    success: bool = Field(False, description="Always False for errors")
    error: str = Field(..., description="Error message")
    detail: str | None = Field(None, description="Detailed error information")
    code: str | None = Field(None, description="Error code for programmatic handling")


class PaginatedResponse[T](BaseModel):
    """
    Generic paginated response model.

    Provides consistent pagination structure for list endpoints.
    """

    items: list[T] = Field(default_factory=list, description="List of items")
    total: int = Field(0, description="Total number of items")
    page: int = Field(1, description="Current page number")
    page_size: int = Field(10, description="Items per page")
    has_next: bool = Field(False, description="Whether more pages exist")
    has_prev: bool = Field(False, description="Whether previous pages exist")

    @property
    def total_pages(self) -> int:
        """Calculate total number of pages."""
        if self.page_size <= 0:
            return 0
        return (self.total + self.page_size - 1) // self.page_size


class CacheMetadata(BaseModel):
    """Metadata about cache status for a response."""

    cached: bool = Field(False, description="Whether response was from cache")
    cache_key: str | None = Field(None, description="Cache key used")
    ttl: int | None = Field(None, description="Cache TTL in seconds")
    expires_at: str | None = Field(None, description="Cache expiration time")


class RequestMetadata(BaseModel):
    """Metadata about the request processing."""

    request_id: str | None = Field(None, description="Unique request identifier")
    processing_time_ms: float | None = Field(None, description="Processing time in ms")
    rate_limit_remaining: int | None = Field(None, description="Remaining rate limit")


class EnhancedResponse(BaseModel):
    """
    Enhanced response with metadata for debugging and monitoring.

    Extends BaseAPIResponse with cache and request metadata.
    """

    success: bool = Field(True, description="Whether the request was successful")
    data: Any | None = Field(None, description="Response payload")
    cache: CacheMetadata | None = Field(None, description="Cache information")
    metadata: RequestMetadata | None = Field(None, description="Request metadata")
