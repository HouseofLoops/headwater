"""
Central schemas package for shared enums, models, and response types.

Import specific modules directly to avoid circular imports:
    from app.schemas.enums import OutputFormat
    from app.schemas.responses import BaseAPIResponse
"""

# Re-exports for convenience (lazy import to avoid circular deps)
__all__ = [
    # Enums
    "TimeframeEnum",
    "HumanFriendlyBatchPeriod",
    "StandardTimeframe",
    "CustomIntervalTimeframe",
    "OutputFormat",
    "ClientType",
    "DataSource",
    "SafeSearch",
    "SearchClient",
    # Responses
    "BaseAPIResponse",
    "PaginatedResponse",
    "ErrorResponse",
    "CacheMetadata",
    "RequestMetadata",
    "EnhancedResponse",
]
