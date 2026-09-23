"""Request and response models for the Google Maps endpoints.

Kept together because several endpoint modules share them, and because the
models carry the URL validation that keeps ``page.goto()`` off the container
network -- that guard belongs with the field it protects, not with whichever
handler happens to read it.
"""

import math
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, validator

from app.api.google_maps.common import validate_maps_url

# A Maps search opens and reads each place in turn, measured at ~12s per result.
# The largest timeout any search endpoint accepts is 600s, so the most results a
# request could ever finish is 600 / 12 = 50, and a run that comes in 10% slower
# than that mean has to fit too: floor(600 / (12 * 1.1)) = 45.
#
# The previous ceiling was 100, which needs 100 * 12 = 1200s -- twice the largest
# timeout on offer. Such a request could not succeed at any setting; it held a
# browser and a concurrency permit for the full timeout and then failed.
SECONDS_PER_RESULT = 12
MAX_SEARCH_TIMEOUT_SECONDS = 600
SLOW_RUN_MARGIN = 1.1
MAX_RESULTS_CEILING = int(MAX_SEARCH_TIMEOUT_SECONDS / (SECONDS_PER_RESULT * SLOW_RUN_MARGIN))


def seconds_needed_for(max_results: int) -> int:
    """Seconds a search of this size needs, including the slow-run margin."""
    return math.ceil(max_results * SECONDS_PER_RESULT * SLOW_RUN_MARGIN)


def affordable_results_within(timeout: int) -> int:
    """Largest result count that fits in ``timeout`` at the measured per-result cost."""
    return max(1, int(timeout / (SECONDS_PER_RESULT * SLOW_RUN_MARGIN)))


class SearchRequest(BaseModel):
    """Request model for places search."""

    query: str = Field(
        ...,
        description="Search query (e.g., 'restaurants in New York', 'coffee shops near Times Square')",
        min_length=3,
        max_length=500,
        examples=["restaurants in New York", "coffee shops near Times Square"],
    )
    language: str = Field("en", description="Language code for results (ISO 639-1)", examples=["en", "es", "fr", "de"])
    max_results: int = Field(
        20,
        ge=1,
        le=MAX_RESULTS_CEILING,
        description=(
            f"Maximum number of results to return. Capped at {MAX_RESULTS_CEILING}: "
            f"each result costs ~{SECONDS_PER_RESULT}s and the largest accepted "
            f"timeout is {MAX_SEARCH_TIMEOUT_SECONDS}s."
        ),
    )
    depth: int = Field(1, ge=1, le=3, description="Crawl depth for pagination (1=first page only, higher=more results)")
    email_extraction: bool = Field(
        False, description="Extract emails from business websites (slower but provides contact info)"
    )
    zoom: int = Field(15, ge=1, le=21, description="Map zoom level (1=world, 21=building level)")
    geo_coordinates: str | None = Field(
        None, description="Search center coordinates (format: 'lat,lng')", examples=["40.7128,-74.0060"]
    )

    class Config:
        json_schema_extra = {
            "example": {
                "query": "coffee shops in Manhattan",
                "language": "en",
                "max_results": 20,
                "depth": 1,
                "email_extraction": False,
                "zoom": 15,
            }
        }


class JobResponse(BaseModel):
    """Response model for job creation."""

    success: bool
    job_id: str | None = None
    status: str
    message: str | None = None
    estimated_time: str | None = None


class ReviewTopic(BaseModel):
    """Model for a review topic/keyword."""

    topic: str = Field(
        ..., description="Topic or keyword mentioned in reviews", examples=["drive thru", "breakfast", "coffee"]
    )
    count: int = Field(..., description="Number of reviews mentioning this topic", examples=[102, 45, 23])


class RelatedPlace(BaseModel):
    """Model for a related place from 'People also search for'."""

    name: str = Field(..., description="Name of the related business")
    rating: float | None = Field(None, description="Star rating (1-5)")
    review_count: int | None = Field(None, description="Number of reviews")
    category: str | None = Field(None, description="Business category")


class PlaceResult(BaseModel):
    """
    Model for a single place result.

    Contains comprehensive business information extracted from Google Maps,
    including basic details, reviews, service options, and engagement data.
    """

    # Basic Information
    place_id: str | None = Field(None, description="Unique Google Maps place identifier (CID)")
    name: str | None = Field(None, description="Business name", examples=["Starbucks", "McDonald's"])
    address: str | None = Field(None, description="Full street address")
    phone: str | None = Field(None, description="Phone number with country code")
    website: str | None = Field(None, description="Business website URL")

    # Location
    latitude: float | None = Field(None, description="Geographic latitude coordinate", examples=[45.5231])
    longitude: float | None = Field(None, description="Geographic longitude coordinate", examples=[-122.6765])
    plus_code: str | None = Field(
        None, description="Google Plus Code for precise location", examples=["85HQ+XX Portland, Oregon"]
    )
    google_maps_url: str | None = Field(None, description="Direct link to Google Maps page")

    # Business Classification
    category: str | None = Field(
        None, description="Primary business category", examples=["Coffee shop", "Restaurant", "Hotel"]
    )
    description: str | None = Field(None, description="Business description or 'About' text")

    # Ratings & Reviews
    rating: float | None = Field(None, description="Average star rating (1.0-5.0)", ge=1.0, le=5.0, examples=[4.2])
    review_count: int | None = Field(None, description="Total number of reviews", examples=[1523])
    review_summary: dict | None = Field(
        None,
        description="Star breakdown showing count per rating level",
        examples=[{"5_star": 474, "4_star": 120, "3_star": 45, "2_star": 15, "1_star": 10}],
    )
    review_topics: list[dict] | None = Field(
        None,
        description="Keywords frequently mentioned in reviews with counts",
        examples=[[{"topic": "drive thru", "count": 102}, {"topic": "breakfast", "count": 45}]],
    )
    sample_reviews: list[str] | None = Field(None, description="Sample review quotes displayed prominently")
    reviews: list[dict] | None = Field(None, description="Full review data (when available)")

    # Pricing
    price_level: str | None = Field(None, description="Price indicator ($, $$, $$$, $$$$)")
    price_per_person: str | None = Field(None, description="Estimated cost per person", examples=["$1–10", "$10–25"])

    # Hours & Availability
    hours: dict | None = Field(
        None,
        description="Operating hours by day of week",
        examples=[{"Monday": ["6 AM–9 PM"], "Tuesday": ["6 AM–9 PM"]}],
    )
    is_open_now: bool | None = Field(None, description="Whether currently open")
    popular_times: dict | None = Field(
        None,
        description="Hourly busy percentages by day",
        examples=[{"Saturday": [{"hour": "10 AM", "busy_percent": 45}, {"hour": "12 PM", "busy_percent": 85}]}],
    )

    # Live Wait Time & Busyness
    wait_time_minutes: int | None = Field(
        None, description="Current or typical wait time in minutes", examples=[15, 30, 45]
    )
    wait_time_raw: str | None = Field(
        None, description="Raw wait time text from Google Maps", examples=["Usually 15 min wait", "Live: 20 min wait"]
    )
    live_busyness: str | None = Field(
        None,
        description="Live busyness indicator from Google Maps",
        examples=["Live: Busier than usual", "Live: Not too busy", "Live: As busy as it gets"],
    )
    typical_busyness: str | None = Field(
        None,
        description="Typical busyness level at current time",
        examples=["Usually not too busy", "Usually a little busy", "Usually not busy"],
    )

    # Service Options & Features
    service_options: list[str] | None = Field(
        None,
        description="Available service types",
        examples=[["Dine-In", "Drive-Through", "Delivery", "Takeout", "Curbside Pickup"]],
    )
    accessibility: list[str] | None = Field(None, description="Accessibility features available")
    amenities: list[str] | None = Field(None, description="Available amenities and highlights")

    # Media & Links
    photos: list[str] | None = Field(None, description="Photo URLs (high resolution)")
    menu_link: str | None = Field(None, description="Link to menu page")
    order_link: str | None = Field(None, description="Link to online ordering")
    reserve_link: str | None = Field(None, description="Link to make reservations")

    # Related Content
    related_places: list[dict] | None = Field(
        None,
        description="'People also search for' suggestions",
        examples=[[{"name": "Burger King", "rating": 3.3, "review_count": 1084, "category": "Restaurant"}]],
    )

    # Contact Information (from email extraction)
    emails: list[str] | None = Field(None, description="Extracted email addresses (requires email_extraction=true)")
    social_media: dict | None = Field(
        None,
        description="Social media profile links",
        examples=[{"facebook": "https://facebook.com/...", "instagram": "https://instagram.com/..."}],
    )

    class Config:
        json_schema_extra = {
            "example": {
                "place_id": "0x54950a0d7f8a85e5:0x1234567890abcdef",
                "name": "Starbucks",
                "address": "123 Main St, Portland, OR 97201",
                "phone": "+1 503-555-0100",
                "website": "https://www.starbucks.com",
                "latitude": 45.5231,
                "longitude": -122.6765,
                "category": "Coffee shop",
                "rating": 4.2,
                "review_count": 523,
                "price_level": "$$",
                "service_options": ["Dine-In", "Drive-Through", "Delivery"],
                "review_topics": [{"topic": "coffee", "count": 89}, {"topic": "drive thru", "count": 45}],
                "hours": {"Monday": ["5:30 AM–8 PM"], "Tuesday": ["5:30 AM–8 PM"]},
                "is_open_now": True,
                "menu_link": "https://www.starbucks.com/menu",
            }
        }


class SearchResponse(BaseModel):
    """Response model for search results."""

    success: bool
    query: str
    total_results: int
    places: list[dict]
    message: str | None = None
    job_id: str | None = None
    timestamp: str


class NearbySearchRequest(BaseModel):
    """Request model for nearby search."""

    latitude: float = Field(..., description="Center latitude", ge=-90, le=90, examples=[40.7128])
    longitude: float = Field(..., description="Center longitude", ge=-180, le=180, examples=[-74.0060])
    radius_meters: int = Field(1000, ge=100, le=50000, description="Search radius in meters")
    query: str | None = Field(None, description="Optional filter query (e.g., 'restaurants')")
    language: str = Field("en", description="Language code")
    max_results: int = Field(20, ge=1, le=100, description="Maximum results")


class PlaceLookupRequest(BaseModel):
    """Request model for place lookup by URL or ID.

    ``url`` is fed to Playwright's ``page.goto()`` inside the container
    network and the resulting DOM is returned to the caller, so an
    unconstrained string here is a server-side request forgery primitive.
    It is validated against the Google Maps host allow-list before the model
    will construct.
    """

    url: str | None = Field(None, description="Google Maps URL", examples=["https://www.google.com/maps/place/..."])
    place_id: str | None = Field(
        None, description="Google Place ID (CID)", examples=["0x89c259af18b60947:0x8c5e3c1d36e36e0a"]
    )

    @validator("url")
    def url_must_be_an_allowed_google_maps_url(cls, v):
        """Reject any URL that is not a Google Maps URL we may fetch."""
        return validate_maps_url(v)


class BulkSearchRequest(BaseModel):
    """Request model for bulk search operations."""

    queries: list[str] = Field(..., min_items=1, max_items=50, description="List of search queries")
    language: str = Field("en", description="Language code")
    max_results_per_query: int = Field(10, ge=1, le=50, description="Max results per query")


class ReviewsRequest(BaseModel):
    """Request model for reviews endpoint."""

    sort_by: str = Field(
        "most_relevant",
        description="Sort order",
        examples=["most_relevant", "newest", "highest_rating", "lowest_rating"],
    )
    limit: int = Field(50, ge=1, le=200, description="Number of reviews to fetch")
    offset: int = Field(0, ge=0, description="Pagination offset")
    min_rating: int | None = Field(None, ge=1, le=5, description="Minimum star rating filter")
    include_owner_responses: bool = Field(True, description="Include business owner responses")


class PhotosRequest(BaseModel):
    """Request model for photos endpoint."""

    max_photos: int = Field(20, ge=1, le=100, description="Maximum photos to return")
    size: str = Field("large", description="Photo size", examples=["thumbnail", "medium", "large", "original"])
    category: str | None = Field(
        None, description="Photo category filter", examples=["all", "food", "interior", "exterior", "menu"]
    )


class QARequest(BaseModel):
    """Request model for Q&A endpoint."""

    limit: int = Field(20, ge=1, le=100, description="Maximum Q&A pairs to return")
    include_answers: bool = Field(True, description="Include answers for each question")


class ReviewAnalyticsRequest(BaseModel):
    """Request model for review analytics."""

    time_period: str = Field(
        "all", description="Time period for analysis", examples=["week", "month", "quarter", "year", "all"]
    )
    include_sentiment: bool = Field(True, description="Include sentiment analysis")
    include_trends: bool = Field(True, description="Include rating trends over time")
    include_keywords: bool = Field(True, description="Include keyword extraction")


class CompetitorRequest(BaseModel):
    """Request model for competitor analysis."""

    latitude: float = Field(..., ge=-90, le=90, description="Center latitude")
    longitude: float = Field(..., ge=-180, le=180, description="Center longitude")
    category: str = Field(..., description="Business category", examples=["restaurants", "coffee shops", "hotels"])
    radius_meters: int = Field(2000, ge=500, le=20000, description="Search radius")
    max_competitors: int = Field(10, ge=1, le=30, description="Maximum competitors to analyze")


class MonitorRequest(BaseModel):
    """Request model for place monitoring."""

    place_id: str | None = Field(None, description="Place ID to monitor")
    url: str | None = Field(None, description="Google Maps URL to monitor")
    webhook_url: str | None = Field(None, description="Webhook URL for change notifications")
    check_interval_hours: int = Field(24, ge=1, le=168, description="Check interval in hours")
    track_fields: list[str] = Field(["rating", "review_count", "hours"], description="Fields to track for changes")

    @validator("url")
    def url_must_be_an_allowed_google_maps_url(cls, v):
        """Same sink as ``PlaceLookupRequest.url``.

        A monitor re-fetches this URL on a schedule, so an unguarded value is a
        *repeating* SSRF. ``webhook_url`` is deliberately not validated here --
        it is an outbound callback to a customer-chosen host and needs its own
        policy, not the Maps allow-list.
        """
        return validate_maps_url(v)


class WebhookRequest(BaseModel):
    """Request model for webhook registration."""

    url: str = Field(..., description="Webhook URL to receive notifications")
    events: list[str] = Field(
        ["job.completed", "job.failed"],
        description="Events to subscribe to",
        examples=[["job.completed", "job.failed", "monitor.changed"]],
    )
    secret: str | None = Field(None, description="Secret for signing webhook payloads")


class DirectionsRequest(BaseModel):
    """Request model for directions."""

    origin_lat: float = Field(..., ge=-90, le=90, description="Origin latitude")
    origin_lng: float = Field(..., ge=-180, le=180, description="Origin longitude")
    destination_lat: float = Field(..., ge=-90, le=90, description="Destination latitude")
    destination_lng: float = Field(..., ge=-180, le=180, description="Destination longitude")
    mode: str = Field("driving", description="Travel mode", examples=["driving", "walking", "transit", "bicycling"])
    alternatives: bool = Field(False, description="Return alternative routes")
    avoid: list[str] | None = Field(None, description="Features to avoid", examples=[["tolls", "highways", "ferries"]])


class GeocodeRequest(BaseModel):
    """Request model for batch geocoding."""

    addresses: list[str] = Field(..., min_items=1, max_items=100, description="List of addresses to geocode")


class MenuExtractionRequest(BaseModel):
    """Request model for menu extraction."""

    include_prices: bool = Field(True, description="Include prices in extraction")
    include_descriptions: bool = Field(True, description="Include item descriptions")
    categorize: bool = Field(True, description="Categorize menu items")


class AutocompleteRequest(BaseModel):
    """Request model for autocomplete."""

    input: str = Field(..., min_length=2, max_length=200, description="Search input for autocomplete")
    types: str | None = Field(None, description="Place types filter", examples=["establishment", "geocode", "address"])
    latitude: float | None = Field(None, ge=-90, le=90, description="Bias latitude")
    longitude: float | None = Field(None, ge=-180, le=180, description="Bias longitude")
    radius_meters: int | None = Field(None, ge=1, le=50000, description="Bias radius")


class ExportFormat(str, Enum):
    """Supported export formats."""

    JSON = "json"
    CSV = "csv"
    EXCEL = "xlsx"
    JSON_LINES = "jsonl"


class ReviewItem(BaseModel):
    """Model for a single review."""

    author_name: str = Field(..., description="Reviewer name")
    author_url: str | None = Field(None, description="Link to reviewer profile")
    rating: int = Field(..., ge=1, le=5, description="Star rating")
    text: str = Field(..., description="Review text")
    time: str = Field(..., description="Review timestamp")
    relative_time: str | None = Field(None, description="Relative time (e.g., '3 days ago')")
    language: str | None = Field(None, description="Review language")
    owner_response: dict[str, str] | None = Field(None, description="Business owner response")


class PhotoItem(BaseModel):
    """Model for a photo."""

    url: str = Field(..., description="Photo URL")
    width: int | None = Field(None, description="Image width")
    height: int | None = Field(None, description="Image height")
    contributor: str | None = Field(None, description="Photo contributor")
    category: str | None = Field(None, description="Photo category")


class QAItem(BaseModel):
    """Model for a Q&A pair."""

    question: str = Field(..., description="Question text")
    question_author: str | None = Field(None, description="Question author")
    question_time: str | None = Field(None, description="Question timestamp")
    answers: list[dict[str, Any]] | None = Field(None, description="List of answers")
    answer_count: int = Field(0, description="Number of answers")


class AutocompleteResult(BaseModel):
    """Model for autocomplete result."""

    description: str = Field(..., description="Full place description")
    place_id: str | None = Field(None, description="Place ID")
    main_text: str = Field(..., description="Main text (place name)")
    secondary_text: str | None = Field(None, description="Secondary text (location)")
    types: list[str] | None = Field(None, description="Place types")


class DirectionsStep(BaseModel):
    """Model for a directions step."""

    instruction: str = Field(..., description="Navigation instruction")
    distance: str = Field(..., description="Distance for this step")
    duration: str = Field(..., description="Duration for this step")
    travel_mode: str = Field(..., description="Travel mode")


class DirectionsRoute(BaseModel):
    """Model for a directions route."""

    summary: str = Field(..., description="Route summary")
    distance: str = Field(..., description="Total distance")
    duration: str = Field(..., description="Total duration")
    steps: list[DirectionsStep] = Field(..., description="Navigation steps")
    polyline: str | None = Field(None, description="Encoded polyline")


class GeocodeResult(BaseModel):
    """Model for a geocode result."""

    address: str = Field(..., description="Input address")
    latitude: float | None = Field(None, description="Result latitude")
    longitude: float | None = Field(None, description="Result longitude")
    formatted_address: str | None = Field(None, description="Formatted address")
    place_id: str | None = Field(None, description="Place ID")
    success: bool = Field(..., description="Whether geocoding succeeded")
    error: str | None = Field(None, description="Error message if failed")


class MenuItem(BaseModel):
    """Model for a menu item."""

    name: str = Field(..., description="Item name")
    description: str | None = Field(None, description="Item description")
    price: str | None = Field(None, description="Item price")
    category: str | None = Field(None, description="Menu category")
    dietary_info: list[str] | None = Field(None, description="Dietary information")


class CompetitorResult(BaseModel):
    """Model for a competitor in analysis."""

    name: str = Field(..., description="Business name")
    rating: float | None = Field(None, description="Average rating")
    review_count: int | None = Field(None, description="Total reviews")
    price_level: str | None = Field(None, description="Price level")
    distance_meters: int | None = Field(None, description="Distance from center")
    strengths: list[str] | None = Field(None, description="Identified strengths")
    weaknesses: list[str] | None = Field(None, description="Identified weaknesses")


class MonitorStatus(BaseModel):
    """Model for monitor status."""

    monitor_id: str = Field(..., description="Monitor ID")
    place_id: str = Field(..., description="Monitored place ID")
    status: str = Field(..., description="Monitor status", examples=["active", "paused", "deleted"])
    last_check: str | None = Field(None, description="Last check timestamp")
    next_check: str | None = Field(None, description="Next scheduled check")
    changes_detected: int = Field(0, description="Number of changes detected")


class WebhookStatus(BaseModel):
    """Model for webhook status."""

    webhook_id: str = Field(..., description="Webhook ID")
    url: str = Field(..., description="Webhook URL")
    events: list[str] = Field(..., description="Subscribed events")
    status: str = Field(..., description="Webhook status")
    last_triggered: str | None = Field(None, description="Last trigger timestamp")
    success_count: int = Field(0, description="Successful deliveries")
    failure_count: int = Field(0, description="Failed deliveries")


class GridSearchRequest(BaseModel):
    """Request model for grid-based search."""

    query: str = Field(..., description="Search query (e.g., 'restaurants')", min_length=1, max_length=500)
    center_lat: float = Field(..., ge=-90, le=90, description="Center latitude")
    center_lng: float = Field(..., ge=-180, le=180, description="Center longitude")
    radius_km: float = Field(default=5.0, ge=0.1, le=50, description="Search radius in kilometers")
    grid_size: int = Field(default=5, ge=3, le=11, description="Grid dimension (5 = 5x5 = 25 search points)")
    max_results_per_point: int = Field(default=10, ge=1, le=20, description="Maximum results per grid point")
    language: str = Field(default="en", description="Language code")

    class Config:
        json_schema_extra = {
            "example": {
                "query": "restaurants",
                "center_lat": 45.3807,
                "center_lng": -122.5940,
                "radius_km": 2.0,
                "grid_size": 5,
                "max_results_per_point": 10,
                "language": "en",
            }
        }


class BoundingBoxRequest(BaseModel):
    """Request model for bounding box search."""

    query: str = Field(..., description="Search query", min_length=1, max_length=500)
    north_lat: float = Field(..., ge=-90, le=90, description="Top boundary (max latitude)")
    south_lat: float = Field(..., ge=-90, le=90, description="Bottom boundary (min latitude)")
    east_lng: float = Field(..., ge=-180, le=180, description="Right boundary (max longitude)")
    west_lng: float = Field(..., ge=-180, le=180, description="Left boundary (min longitude)")
    grid_density: int = Field(default=5, ge=3, le=11, description="Grid points per side")
    max_results_per_point: int = Field(default=10, ge=1, le=20, description="Max results per point")
    language: str = Field(default="en", description="Language code")

    class Config:
        json_schema_extra = {
            "example": {
                "query": "coffee shops",
                "north_lat": 45.42,
                "south_lat": 45.35,
                "east_lng": -122.55,
                "west_lng": -122.65,
                "grid_density": 5,
                "max_results_per_point": 10,
            }
        }


class LocationSearchRequest(BaseModel):
    """Request model for location-based search."""

    query: str = Field(..., description="Search query", min_length=1, max_length=500)
    location: str = Field(
        ...,
        description="Location name (city, address, or ZIP code)",
        examples=["Portland, OR", "97027", "123 Main St, Seattle, WA"],
    )
    radius_km: float = Field(default=5.0, ge=0.1, le=50, description="Search radius in km")
    grid_size: int = Field(default=5, ge=3, le=11, description="Grid dimension")
    max_results_per_point: int = Field(default=10, ge=1, le=20, description="Max results per point")
    language: str = Field(default="en", description="Language code")

    class Config:
        json_schema_extra = {
            "example": {"query": "restaurants", "location": "Gladstone, OR", "radius_km": 2.0, "grid_size": 5}
        }
