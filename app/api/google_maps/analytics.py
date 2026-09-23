"""Derived analytics: review sentiment and competitor comparison.

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
    CompetitorRequest,
)
from app.core.auth import get_api_key
from app.core.log_safety import scrub
from app.core.rate_limiter import rate_limit
from app.services.google_maps_service import google_maps_service

logger = logging.getLogger(__name__)

router = APIRouter(route_class=SafeUrlValidationRoute)


@router.get(
    "/place/{place_id}/analytics", summary="Get review analytics", response_description="Review analysis and insights"
)
async def get_review_analytics(
    place_id: str = Path(..., description="Place ID"),
    time_period: str = Query("all", description="Time period"),
    include_sentiment: bool = Query(True, description="Include sentiment analysis"),
    include_trends: bool = Query(True, description="Include rating trends"),
    include_keywords: bool = Query(True, description="Include keyword extraction"),
    api_key: str = Depends(get_api_key),
    rate_limit_check: None = Depends(rate_limit),
):
    """
    Get analytics and insights for a place's reviews.

    **Analysis Includes:**
    - Rating distribution and trends
    - Sentiment analysis (positive/negative/neutral)
    - Keyword extraction and frequency
    - Response rate from owner
    - Peak review times

    **Time Periods:**
    - `week` - Last 7 days
    - `month` - Last 30 days
    - `quarter` - Last 90 days
    - `year` - Last 365 days
    - `all` - All time (default)
    """
    logger.info("Get analytics for place: %s", scrub(place_id))

    try:
        result = await google_maps_service.get_review_analytics(
            place_id=place_id,
            time_period=time_period,
            include_sentiment=include_sentiment,
            include_trends=include_trends,
            include_keywords=include_keywords,
        )

        if result.get("error"):
            raise upstream_error(result, "Failed to get analytics")

        return {
            "success": True,
            "place_id": place_id,
            "time_period": time_period,
            "analytics": result.get("analytics", {}),
            "timestamp": datetime.now().isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Analytics error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL)


@router.post("/competitors", summary="Analyze competitors", response_description="Competitor comparison")
async def analyze_competitors(
    request: CompetitorRequest, api_key: str = Depends(get_api_key), rate_limit_check: None = Depends(rate_limit)
):
    """
    Find and analyze competitors in an area.

    **Analysis Includes:**
    - Nearby businesses in same category
    - Rating comparisons
    - Review volume comparison
    - Price level comparison
    - Identified strengths/weaknesses

    **Example:**
    ```json
    {
        "latitude": 40.7128,
        "longitude": -74.0060,
        "category": "coffee shops",
        "radius_meters": 2000,
        "max_competitors": 10
    }
    ```
    """
    # Caller coordinates are deliberately NOT logged. Rounding them was not
    # enough: a location is personal data, and the endpoint name plus the
    # category already give the operational signal we actually use.
    logger.info("Competitor analysis for category=%s", scrub(request.category))

    try:
        result = await google_maps_service.analyze_competitors(
            latitude=request.latitude,
            longitude=request.longitude,
            category=request.category,
            radius_meters=request.radius_meters,
            max_competitors=request.max_competitors,
        )

        if result.get("error"):
            raise upstream_error(result, "Competitor analysis failed")

        return {
            "success": True,
            "center": {"latitude": request.latitude, "longitude": request.longitude},
            "category": request.category,
            "radius_meters": request.radius_meters,
            "competitors": result.get("competitors", []),
            "summary": result.get("summary", {}),
            "timestamp": datetime.now().isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Competitor analysis error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL)
