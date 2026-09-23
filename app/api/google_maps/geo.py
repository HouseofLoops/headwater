"""Directions and geocoding.

Mounted onto ``google_maps_router`` by this package's ``__init__``; the paths
declared here are relative to the ``/google-maps`` prefix applied there.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.google_maps.common import (
    INTERNAL_ERROR_DETAIL,
    SafeUrlValidationRoute,
    upstream_error,
)
from app.api.google_maps.schemas import (
    DirectionsRequest,
    GeocodeRequest,
)
from app.core.auth import get_api_key
from app.core.rate_limiter import rate_limit
from app.services.google_maps_service import google_maps_service

logger = logging.getLogger(__name__)

router = APIRouter(route_class=SafeUrlValidationRoute)


@router.post("/directions", summary="Get directions", response_description="Route information")
async def get_directions(
    request: DirectionsRequest, api_key: str = Depends(get_api_key), rate_limit_check: None = Depends(rate_limit)
):
    """
    Get directions between two locations.

    **Travel Modes:**
    - `driving` - By car (default)
    - `walking` - On foot
    - `transit` - Public transportation
    - `bicycling` - By bicycle

    **Avoid Options:**
    - `tolls` - Avoid toll roads
    - `highways` - Avoid highways
    - `ferries` - Avoid ferries

    Returns route with step-by-step directions, distance, and duration.
    """
    # Caller coordinates are deliberately NOT logged; see analytics.py. An
    # origin/destination pair is a movement record -- more sensitive than a
    # single point, not less.
    logger.info("Get directions requested")

    try:
        result = await google_maps_service.get_directions(
            origin_lat=request.origin_lat,
            origin_lng=request.origin_lng,
            destination_lat=request.destination_lat,
            destination_lng=request.destination_lng,
            mode=request.mode,
            alternatives=request.alternatives,
            avoid=request.avoid,
        )

        if result.get("error"):
            raise upstream_error(result, "Failed to get directions")

        return {
            "success": True,
            "origin": {"latitude": request.origin_lat, "longitude": request.origin_lng},
            "destination": {"latitude": request.destination_lat, "longitude": request.destination_lng},
            "mode": request.mode,
            "routes": result.get("routes", []),
            "timestamp": datetime.now().isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Directions error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL) from e


@router.get("/directions", summary="Get directions (GET)", response_description="Route information")
async def get_directions_get(
    origin_lat: float = Query(..., ge=-90, le=90, description="Origin latitude"),
    origin_lng: float = Query(..., ge=-180, le=180, description="Origin longitude"),
    destination_lat: float = Query(..., ge=-90, le=90, description="Destination latitude"),
    destination_lng: float = Query(..., ge=-180, le=180, description="Destination longitude"),
    mode: str = Query("driving", description="Travel mode"),
    alternatives: bool = Query(False, description="Return alternatives"),
    api_key: str = Depends(get_api_key),
    rate_limit_check: None = Depends(rate_limit),
):
    """
    Get directions between two locations (GET version).
    """
    request = DirectionsRequest(
        origin_lat=origin_lat,
        origin_lng=origin_lng,
        destination_lat=destination_lat,
        destination_lng=destination_lng,
        mode=mode,
        alternatives=alternatives,
    )
    return await get_directions(request=request, api_key=api_key, rate_limit_check=rate_limit_check)


@router.post("/geocode", summary="Batch geocode addresses", response_description="Coordinates for addresses")
async def batch_geocode(
    request: GeocodeRequest, api_key: str = Depends(get_api_key), rate_limit_check: None = Depends(rate_limit)
):
    """
    Convert multiple addresses to coordinates.

    **Features:**
    - Up to 100 addresses per request
    - Returns coordinates and formatted addresses
    - Individual error handling per address
    - Place IDs for further lookups

    **Example:**
    ```json
    {
        "addresses": [
            "1600 Amphitheatre Parkway, Mountain View, CA",
            "350 5th Avenue, New York, NY"
        ]
    }
    ```
    """
    logger.info(f"Batch geocode: {len(request.addresses)} addresses")

    try:
        result = await google_maps_service.batch_geocode(addresses=request.addresses)

        if result.get("error"):
            raise upstream_error(result, "Geocoding failed")

        return {
            "success": True,
            "total_addresses": len(request.addresses),
            "successful": result.get("successful", 0),
            "failed": result.get("failed", 0),
            "results": result.get("results", []),
            "timestamp": datetime.now().isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Geocode error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL) from e


@router.get("/geocode", summary="Geocode address (GET)", response_description="Coordinates for address")
async def geocode_get(
    address: str = Query(..., min_length=5, max_length=500, description="Address to geocode"),
    api_key: str = Depends(get_api_key),
    rate_limit_check: None = Depends(rate_limit),
):
    """
    Convert a single address to coordinates.
    """
    result = await batch_geocode(
        request=GeocodeRequest(addresses=[address]), api_key=api_key, rate_limit_check=rate_limit_check
    )

    # batch_geocode returns one entry PER ADDRESS, and a failed address is
    # still an entry -- so `result["results"]` is truthy even when the single
    # address failed. Checking only for the list's presence reported
    # "success": True while wrapping {"success": False, "error": ...}.
    entries = result.get("results") or []
    first = entries[0] if entries else None

    if first is not None and first.get("success", True):
        return {"success": True, "address": address, "result": first, "timestamp": datetime.now().isoformat()}
    else:
        return {
            "success": False,
            "address": address,
            "error": (first or {}).get("error", "Geocoding failed"),
            "timestamp": datetime.now().isoformat(),
        }
