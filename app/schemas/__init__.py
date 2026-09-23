"""
Central schemas package for shared enums, models, and response types.

Import specific modules directly to avoid circular imports:
    from app.schemas.enums import OutputFormat
    from app.schemas.responses import BaseAPIResponse
"""

# Re-exports for convenience. This used to declare these names in __all__
# without importing them (the "lazy import" it promised was never written),
# so `from app.schemas import OutputFormat` raised ImportError. enums and
# responses import only the stdlib and pydantic, so importing them here
# cannot create a cycle.
from app.schemas.enums import (
    ClientType,
    CustomIntervalTimeframe,
    DataSource,
    HumanFriendlyBatchPeriod,
    OutputFormat,
    SafeSearch,
    SearchClient,
    StandardTimeframe,
    TimeframeEnum,
)
from app.schemas.responses import (
    BaseAPIResponse,
    CacheMetadata,
    EnhancedResponse,
    ErrorResponse,
    PaginatedResponse,
    RequestMetadata,
)

__all__ = [
    "BaseAPIResponse",
    "CacheMetadata",
    "ClientType",
    "CustomIntervalTimeframe",
    "DataSource",
    "EnhancedResponse",
    "ErrorResponse",
    "HumanFriendlyBatchPeriod",
    "OutputFormat",
    "PaginatedResponse",
    "RequestMetadata",
    "SafeSearch",
    "SearchClient",
    "StandardTimeframe",
    "TimeframeEnum",
]
