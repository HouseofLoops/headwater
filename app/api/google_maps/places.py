"""Single-place endpoints: details, lookup, reviews, photos, Q&A, menu,
attributes, history and availability.

Mounted onto ``google_maps_router`` by this package's ``__init__``; the paths
declared here are relative to the ``/google-maps`` prefix applied there.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from app.api.google_maps.common import (
    INTERNAL_ERROR_DETAIL,
    SafeUrlValidationRoute,
    upstream_error,
)
from app.api.google_maps.schemas import (
    PlaceLookupRequest,
)
from app.core.auth import get_api_key
from app.core.log_safety import scrub
from app.core.rate_limiter import rate_limit
from app.services.google_maps_service import google_maps_service

logger = logging.getLogger(__name__)

router = APIRouter(route_class=SafeUrlValidationRoute)


@router.get("/place/{place_id}", summary="Get place details by ID", response_description="Comprehensive place details")
async def get_place_by_id(
    place_id: str = Path(..., description="Google Place ID (CID) or URL-encoded place identifier"),
    api_key: str = Depends(get_api_key),
    rate_limit_check: None = Depends(rate_limit),
):
    """
    Get detailed information for a specific place by its ID.

    **Supported ID Formats:**
    - CID: `0x89c259af18b60947:0x8c5e3c1d36e36e0a`
    - ChIJ format: `ChIJN1t_tDeuEmsRUsoyG83frY4`

    Returns comprehensive place data including reviews, hours, service options, and more.
    """
    logger.info("Place lookup by ID: %s", scrub(place_id))

    try:
        result = await google_maps_service.get_place_by_id(place_id)

        if result.get("error"):
            raise upstream_error(result, "Failed to get place details")

        return {
            "success": True,
            "place_id": place_id,
            "place": result.get("place"),
            "timestamp": datetime.now().isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Place lookup error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL) from e


@router.post("/place/lookup", summary="Lookup place by URL or ID", response_description="Place details")
async def lookup_place(
    request: PlaceLookupRequest, api_key: str = Depends(get_api_key), rate_limit_check: None = Depends(rate_limit)
):
    """
    Look up a place by Google Maps URL or Place ID.

    **Examples:**
    - By URL: `{"url": "https://www.google.com/maps/place/..."}`
    - By ID: `{"place_id": "0x89c259af18b60947:0x8c5e3c1d36e36e0a"}`

    Returns comprehensive place details.
    """
    if not request.url and not request.place_id:
        raise HTTPException(status_code=400, detail="Either 'url' or 'place_id' must be provided")

    try:
        result = await google_maps_service.lookup_place(url=request.url, place_id=request.place_id)

        if result.get("error"):
            raise upstream_error(result, "Failed to lookup place")

        return {"success": True, "place": result.get("place"), "timestamp": datetime.now().isoformat()}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Place lookup error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL) from e


@router.get(
    "/place/{place_id}/reviews",
    summary="Get place reviews",
    response_description="Paginated reviews with owner responses",
)
async def get_place_reviews(
    place_id: str = Path(..., description="Place ID"),
    sort_by: str = Query("most_relevant", description="Sort order"),
    limit: int = Query(50, ge=1, le=200, description="Number of reviews"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    min_rating: int | None = Query(None, ge=1, le=5, description="Minimum rating filter"),
    include_owner_responses: bool = Query(True, description="Include owner responses"),
    api_key: str = Depends(get_api_key),
    rate_limit_check: None = Depends(rate_limit),
):
    """
    Get reviews for a specific place.

    **Sort Options:**
    - `most_relevant` - Google's relevance ranking (default)
    - `newest` - Most recent first
    - `highest_rating` - 5 stars first
    - `lowest_rating` - 1 star first

    **Filtering:**
    - Use `min_rating` to filter by minimum star rating
    - Use `include_owner_responses` to include business replies

    Returns paginated reviews with full text, ratings, timestamps, and optional owner responses.
    """
    logger.info("Get reviews for place: %s", scrub(place_id))

    try:
        result = await google_maps_service.get_place_reviews(
            place_id=place_id,
            sort_by=sort_by,
            limit=limit,
            offset=offset,
            min_rating=min_rating,
            include_owner_responses=include_owner_responses,
        )

        if result.get("error"):
            raise upstream_error(result, "Failed to get reviews")

        return {
            "success": True,
            "place_id": place_id,
            "total_reviews": result.get("total_reviews", 0),
            "average_rating": result.get("average_rating"),
            "reviews": result.get("reviews", []),
            "pagination": {"limit": limit, "offset": offset, "has_more": result.get("has_more", False)},
            "timestamp": datetime.now().isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get reviews error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL) from e


@router.get("/place/{place_id}/photos", summary="Get place photos", response_description="Photo URLs with metadata")
async def get_place_photos(
    place_id: str = Path(..., description="Place ID"),
    max_photos: int = Query(20, ge=1, le=100, description="Maximum photos"),
    size: str = Query("large", description="Photo size (thumbnail, medium, large, original)"),
    category: str | None = Query(None, description="Photo category filter"),
    api_key: str = Depends(get_api_key),
    rate_limit_check: None = Depends(rate_limit),
):
    """
    Get photos for a specific place.

    **Size Options:**
    - `thumbnail` - 100x100
    - `medium` - 400x300
    - `large` - 800x600 (default)
    - `original` - Full resolution

    **Category Filter:**
    - `all` - All photos (default)
    - `food` - Food/menu photos
    - `interior` - Inside the business
    - `exterior` - Outside/storefront
    - `menu` - Menu photos

    Returns photo URLs with contributor information when available.
    """
    logger.info("Get photos for place: %s", scrub(place_id))

    try:
        result = await google_maps_service.get_place_photos(
            place_id=place_id, max_photos=max_photos, size=size, category=category
        )

        if result.get("error"):
            raise upstream_error(result, "Failed to get photos")

        return {
            "success": True,
            "place_id": place_id,
            "total_photos": result.get("total_photos", 0),
            "photos": result.get("photos", []),
            "timestamp": datetime.now().isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get photos error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL) from e


@router.get("/place/{place_id}/qa", summary="Get place Q&A", response_description="Questions and answers")
async def get_place_qa(
    place_id: str = Path(..., description="Place ID"),
    limit: int = Query(20, ge=1, le=100, description="Maximum Q&A pairs"),
    include_answers: bool = Query(True, description="Include answers"),
    api_key: str = Depends(get_api_key),
    rate_limit_check: None = Depends(rate_limit),
):
    """
    Get Questions & Answers for a specific place.

    Returns the Q&A section from the Google Maps listing including:
    - Questions asked by users
    - Answers from the business owner and community
    - Answer counts and timestamps
    """
    logger.info("Get Q&A for place: %s", scrub(place_id))

    try:
        result = await google_maps_service.get_place_qa(place_id=place_id, limit=limit, include_answers=include_answers)

        if result.get("error"):
            raise upstream_error(result, "Failed to get Q&A")

        return {
            "success": True,
            "place_id": place_id,
            "total_questions": result.get("total_questions", 0),
            "questions": result.get("questions", []),
            "timestamp": datetime.now().isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get Q&A error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL) from e


@router.get("/place/{place_id}/menu", summary="Extract menu", response_description="Structured menu data")
async def extract_menu(
    place_id: str = Path(..., description="Place ID"),
    include_prices: bool = Query(True, description="Include prices"),
    include_descriptions: bool = Query(True, description="Include descriptions"),
    categorize: bool = Query(True, description="Categorize items"),
    api_key: str = Depends(get_api_key),
    rate_limit_check: None = Depends(rate_limit),
):
    """
    Extract and structure menu information for a restaurant.

    **Extraction Includes:**
    - Menu item names
    - Prices (when available)
    - Descriptions
    - Categories (appetizers, mains, desserts, etc.)
    - Dietary information (vegetarian, gluten-free, etc.)

    Note: Menu availability depends on whether the business has uploaded menu data.
    """
    logger.info("Extract menu for place: %s", scrub(place_id))

    try:
        result = await google_maps_service.extract_menu(
            place_id=place_id,
            include_prices=include_prices,
            include_descriptions=include_descriptions,
            categorize=categorize,
        )

        if result.get("error"):
            raise upstream_error(result, "Failed to extract menu")

        return {
            "success": True,
            "place_id": place_id,
            "menu_available": result.get("menu_available", False),
            "menu": result.get("menu", []),
            "categories": result.get("categories", []),
            "timestamp": datetime.now().isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Menu extraction error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL) from e


@router.get(
    "/place/{place_id}/attributes", summary="Get place attributes", response_description="Detailed place attributes"
)
async def get_place_attributes(
    place_id: str = Path(..., description="Place ID"),
    api_key: str = Depends(get_api_key),
    rate_limit_check: None = Depends(rate_limit),
):
    """
    Get detailed attributes for a place.

    **Attribute Categories:**
    - **Accessibility:** Wheelchair access, elevators, parking
    - **Amenities:** WiFi, restrooms, outdoor seating
    - **Payments:** Accepted payment methods
    - **Service Options:** Dine-in, takeout, delivery, etc.
    - **Highlights:** Featured attributes
    - **Crowd:** Typical crowd information
    - **Planning:** Reservation requirements, wait times
    """
    logger.info("Get attributes for place: %s", scrub(place_id))

    try:
        result = await google_maps_service.get_place_attributes(place_id=place_id)

        if result.get("error"):
            raise upstream_error(result, "Failed to get attributes")

        return {
            "success": True,
            "place_id": place_id,
            "attributes": result.get("attributes", {}),
            "timestamp": datetime.now().isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get attributes error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL) from e


@router.get("/place/{place_id}/history", summary="Get place history", response_description="Historical data for place")
async def get_place_history(
    place_id: str = Path(..., description="Place ID"),
    field: str | None = Query(None, description="Specific field to get history for"),
    start_date: str | None = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: str | None = Query(None, description="End date (YYYY-MM-DD)"),
    api_key: str = Depends(get_api_key),
    rate_limit_check: None = Depends(rate_limit),
):
    """
    Get historical data for a monitored place.

    **Requires:** Active monitor for the place.

    **Trackable Fields:**
    - `rating` - Rating changes over time
    - `review_count` - Review count changes
    - `hours` - Operating hours changes
    - `all` - All tracked fields

    Returns timestamped snapshots of the tracked fields.
    """
    logger.info("Get history for place: %s", scrub(place_id))

    try:
        result = await google_maps_service.get_place_history(
            place_id=place_id, field=field, start_date=start_date, end_date=end_date, api_key=api_key
        )

        if result.get("error"):
            raise upstream_error(result, "Failed to get history")

        return {
            "success": True,
            "place_id": place_id,
            "field": field or "all",
            "history": result.get("history", []),
            "timestamp": datetime.now().isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get history error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL) from e


@router.get(
    "/place/{place_id}/availability",
    summary="Check reservation availability",
    response_description="Available reservation times",
)
async def check_availability(
    place_id: str = Path(..., description="Place ID"),
    date: str = Query(..., description="Date to check (YYYY-MM-DD)"),
    party_size: int = Query(2, ge=1, le=20, description="Number of guests"),
    api_key: str = Depends(get_api_key),
    rate_limit_check: None = Depends(rate_limit),
):
    """
    Check reservation availability for a restaurant.

    **Note:** Availability data depends on whether the restaurant
    has integrated reservation systems (Reserve with Google).

    Returns available time slots for the specified date and party size.
    """
    logger.info("Check availability for place: %s on %s", scrub(place_id), scrub(date))

    try:
        result = await google_maps_service.check_availability(place_id=place_id, date=date, party_size=party_size)

        if result.get("error"):
            raise upstream_error(result, "Failed to check availability")

        return {
            "success": True,
            "place_id": place_id,
            "date": date,
            "party_size": party_size,
            "reservations_available": result.get("reservations_available", False),
            "time_slots": result.get("time_slots", []),
            "booking_url": result.get("booking_url"),
            "timestamp": datetime.now().isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Check availability error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL) from e
