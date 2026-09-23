"""
Failure signals and field requirements for the Google Maps scraper.
"""
from typing import Optional, Dict, Any, Sequence
# -- Failure signals ----------------------------------------------------------


class ScraperError(RuntimeError):
    """Base class for scraper failures that must not look like empty success."""

    status_code = 502


class PlaceExtractionError(ScraperError):
    """A single place could not be extracted (required fields missing).

    Attributes:
        missing: Required field names that no selector matched.
    """

    status_code = 502

    def __init__(self, message: str, *, missing: Optional[Sequence[str]] = None) -> None:
        super().__init__(message)
        self.missing = list(missing or [])


class SelectorsStaleError(ScraperError):
    """Every candidate place failed to extract -- the page shape has changed.

    This is the error that exists so a total scraping outage cannot be
    reported as ``{"success": true, "places": []}``. Routers should map it to
    503: the upstream is present but this service can no longer read it.

    Attributes:
        attempted: Number of candidate places the scraper tried to extract.
        extracted: Number it succeeded on (zero, or this is not raised).
        missing: Union of required fields that failed to match.
    """

    status_code = 503

    def __init__(
        self,
        message: str,
        *,
        attempted: int = 0,
        extracted: int = 0,
        missing: Optional[Sequence[str]] = None,
    ) -> None:
        super().__init__(message)
        self.attempted = attempted
        self.extracted = extracted
        self.missing = list(missing or [])

    def to_dict(self) -> Dict[str, Any]:
        """Structured payload a router can return alongside a 503."""
        return {
            "error": True,
            "selectors_stale": True,
            "message": str(self),
            "attempted": self.attempted,
            "extracted": self.extracted,
            "missing_fields": self.missing,
        }


#: A place is worthless without these; if none match, the DOM has changed.
REQUIRED_PLACE_FIELDS = ("title",)

#: At least one of these must match for a place to be considered fully
#: extracted. A record with a title scraped out of the URL but no address, no
#: phone, no website and no rating means the details panel did not parse.
CORE_PLACE_FIELDS = (
    "address",
    "phone",
    "website",
    "category",
    "review_rating",
    "review_count",
    "open_hours",
)
