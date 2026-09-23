"""Health of the Google Maps scraper service.

Mounted onto ``google_maps_router`` by this package's ``__init__``; the paths
declared here are relative to the ``/google-maps`` prefix applied there.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends

from app.api.google_maps.common import SafeUrlValidationRoute
from app.core.rate_limiter import rate_limit
from app.services.google_maps_service import google_maps_service

logger = logging.getLogger(__name__)

router = APIRouter(route_class=SafeUrlValidationRoute)


@router.get(
    "/health",
    summary="Check Google Maps scraper health",
    response_description="Health status of the Google Maps scraper service",
)
async def check_health(rate_limit_check: None = Depends(rate_limit)):
    """
    Check if the Google Maps scraper service is healthy and responding.

    Rate limited like every other route: this call reaches into the scraper
    service, so an unmetered health probe is a free way to keep a browser
    busy.

    Uses native Playwright browser automation for scraping.
    Returns the health status, mode (native-playwright), and any error information.
    """
    health = await google_maps_service.health_check()
    return {
        "service": "google-maps-scraper",
        "healthy": health.get("healthy", False),
        "timestamp": datetime.now().isoformat(),
        "details": health,
    }
