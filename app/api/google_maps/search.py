"""Search endpoints: text search, nearby, grid, bounding box, location,
bulk and autocomplete.

Mounted onto ``google_maps_router`` by this package's ``__init__``; the paths
declared here are relative to the ``/google-maps`` prefix applied there.
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.google_maps.common import (
    INTERNAL_ERROR_DETAIL,
    SafeUrlValidationRoute,
    places_from_result,
    upstream_error,
)
from app.api.google_maps.schemas import (
    MAX_RESULTS_CEILING,
    MAX_SEARCH_TIMEOUT_SECONDS,
    SECONDS_PER_RESULT,
    BoundingBoxRequest,
    BulkSearchRequest,
    GridSearchRequest,
    LocationSearchRequest,
    NearbySearchRequest,
    SearchRequest,
    affordable_results_within,
    seconds_needed_for,
)
from app.core.auth import get_api_key
from app.core.log_safety import scrub
from app.core.rate_limiter import rate_limit
from app.services.google_maps_service import google_maps_service
from app.services.record_store import owner_id_for_api_key

logger = logging.getLogger(__name__)

router = APIRouter(route_class=SafeUrlValidationRoute)


from app.core.cache_manager import generate_cache_key, get_cached_or_fetch

# A Maps search costs ~12s per result because each place is opened and read in
# turn, so an identical repeat query cost 40s twice over. Every other module
# here already caches; Maps was the one that did not. Caching the GET path,
# which is what gets re-requested, turns the repeat into a lookup.
MAPS_SEARCH_CACHE_TTL = 3600


def enforce_result_budget(max_results: int, timeout: int) -> None:
    """Reject a size/timeout pair that cannot finish before it starts running.

    At ~12s per result a request for more results than the timeout pays for is
    already lost when it arrives: it holds a browser and a concurrency permit
    for the whole timeout, returns nothing, and blocks other callers meanwhile.
    A 400 that names the budget costs the caller nothing and tells them what to
    ask for instead.
    """
    needed = seconds_needed_for(max_results)
    if needed <= timeout:
        return

    raise HTTPException(
        status_code=400,
        detail=(
            f"max_results={max_results} needs about {needed}s at the measured "
            f"~{SECONDS_PER_RESULT}s per result, but timeout={timeout}s was given. "
            f"Request max_results={affordable_results_within(timeout)} at this timeout, "
            f"or raise timeout to {needed} (maximum {MAX_SEARCH_TIMEOUT_SECONDS})."
        ),
    )


@router.post(
    "/search",
    summary="Search Google Maps places",
    response_description="Search results with place details",
    responses={
        200: {"description": "Search results"},
        400: {"description": "Invalid parameters"},
        401: {"description": "Invalid API key"},
        503: {"description": "Google Maps scraper unavailable"}
    }
)
async def search_places(
    request: SearchRequest,
    wait_for_results: bool = Query(
        True,
        description="Wait for results (True) or return job ID immediately (False)"
    ),
    timeout: int = Query(
        300,
        ge=30,
        le=MAX_SEARCH_TIMEOUT_SECONDS,
        description="Maximum seconds to wait for results (if wait_for_results=True)"
    ),
    api_key: str = Depends(get_api_key),
    rate_limit_check: None = Depends(rate_limit)
):
    """
    Search for places on Google Maps.

    **Features:**
    - Full-text search across Google Maps
    - Up to 45 results per search (see max_results)
    - Comprehensive place details extraction
    - Coordinate-based search centering

    **Data Extracted:**
    - **Basic Info:** Name, address, phone, website, category
    - **Location:** Coordinates, Plus Code, Google Maps URL
    - **Reviews:** Rating, count, star breakdown, topics/keywords, sample quotes
    - **Service Options:** Dine-in, Drive-through, Delivery, Takeout, Curbside pickup
    - **Hours:** Operating hours by day, open/closed status
    - **Popular Times:** Hourly busy percentages (when available)
    - **Links:** Menu, online ordering, reservations
    - **Related:** "People also search for" suggestions
    - **Contact:** Emails and social media (with email_extraction=true)

    **Usage Examples:**
    - `{"query": "restaurants in New York"}` - Basic search
    - `{"query": "coffee near me", "geo_coordinates": "40.7128,-74.0060"}` - Location-based
    - `{"query": "hotels in Paris", "email_extraction": true}` - With email extraction
    - `{"query": "Starbucks Portland Oregon", "max_results": 5}` - Specific business search
    """
    logger.info("Google Maps search: %s", scrub(request.query))

    # Only the blocking form spends the timeout; a job created with
    # wait_for_results=False runs on its own clock.
    if wait_for_results:
        enforce_result_budget(request.max_results, timeout)

    # Check service health first
    health = await google_maps_service.health_check()
    if not health.get("healthy"):
        raise HTTPException(
            status_code=503,
            detail="Google Maps scraper service is unavailable"
        )

    # Stamped on the job so that only this caller can later read or delete it.
    owner = owner_id_for_api_key(api_key)

    try:
        if wait_for_results:
            # Synchronous search - wait for results
            result = await google_maps_service.search_and_wait(
                query=request.query,
                owner=owner,
                language=request.language,
                max_results=request.max_results,
                depth=request.depth,
                email_extraction=request.email_extraction,
                zoom=request.zoom,
                geo_coordinates=request.geo_coordinates,
                timeout=timeout
            )

            if result.get("error"):
                raise upstream_error(result, "Search failed")

            # Process the results
            places = places_from_result(result, "search")

            return {
                "success": True,
                "query": request.query,
                "total_results": len(places),
                "places": places,
                "job_id": result.get("job_id"),
                "timestamp": datetime.now().isoformat()
            }
        else:
            # Async search - return job ID immediately
            job_result = await google_maps_service.create_search_job(
                query=request.query,
                owner=owner,
                language=request.language,
                max_results=request.max_results,
                depth=request.depth,
                email_extraction=request.email_extraction,
                zoom=request.zoom,
                geo_coordinates=request.geo_coordinates
            )

            if job_result.get("error"):
                raise upstream_error(job_result, "Failed to create search job")

            return {
                "success": True,
                "job_id": job_result.get("job_id") or job_result.get("id"),
                "status": "pending",
                "message": "Search job created. Use /jobs/{job_id} to check status.",
                # Was max_results * 2, which under-quoted the wait six-fold
                # against the measured ~12s per result.
                "estimated_time": f"~{request.max_results * SECONDS_PER_RESULT} seconds",
                "timestamp": datetime.now().isoformat()
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Search error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL)


@router.get(
    "/search",
    summary="Search Google Maps places (GET)",
    response_description="Search results with place details"
)
async def search_places_get(
    query: str = Query(
        ...,
        min_length=3,
        max_length=500,
        description="Search query",
        examples=["restaurants in New York"]
    ),
    language: str = Query("en", description="Language code"),
    max_results: int = Query(
        20,
        ge=1,
        le=MAX_RESULTS_CEILING,
        description=f"Maximum results (~{SECONDS_PER_RESULT}s each)"
    ),
    depth: int = Query(1, ge=1, le=3, description="Crawl depth"),
    email_extraction: bool = Query(False, description="Extract emails"),
    zoom: int = Query(15, ge=1, le=21, description="Map zoom level"),
    geo_coordinates: str | None = Query(None, description="Search center (lat,lng)"),
    wait_for_results: bool = Query(True, description="Wait for results"),
    timeout: int = Query(
        300,
        ge=30,
        le=MAX_SEARCH_TIMEOUT_SECONDS,
        description="Timeout in seconds"
    ),
    api_key: str = Depends(get_api_key),
    rate_limit_check: None = Depends(rate_limit)
):
    """
    Search for places on Google Maps (GET version).

    Same functionality as POST /search but using query parameters.
    Returns comprehensive place data including service options, review topics,
    operating hours, and related places.
    """
    request = SearchRequest(
        query=query,
        language=language,
        max_results=max_results,
        depth=depth,
        email_extraction=email_extraction,
        zoom=zoom,
        geo_coordinates=geo_coordinates
    )

    async def run_search():
        return await search_places(
            request=request,
            wait_for_results=wait_for_results,
            timeout=timeout,
            api_key=api_key,
            rate_limit_check=rate_limit_check
        )

    # Only the blocking form is cacheable. wait_for_results=False returns a job
    # id, and caching that would hand every later caller the first job's id.
    if not wait_for_results:
        return await run_search()

    # Checked here as well as inside search_places because timeout is not part
    # of the cache key: a hit would otherwise answer 200 for a combination that
    # answers 400 on a miss, and the same request would behave two ways.
    enforce_result_budget(max_results, timeout)

    cache_key = generate_cache_key(
        "maps_search",
        query=query,
        language=language,
        max_results=max_results,
        depth=depth,
        email_extraction=email_extraction,
        zoom=zoom,
        geo_coordinates=geo_coordinates,
    )
    return await get_cached_or_fetch(cache_key, run_search, ttl=MAPS_SEARCH_CACHE_TTL)


@router.post(
    "/nearby",
    summary="Search nearby places",
    response_description="Places within radius"
)
async def nearby_search(
    request: NearbySearchRequest,
    wait_for_results: bool = Query(True, description="Wait for results"),
    timeout: int = Query(300, ge=30, le=600, description="Timeout in seconds"),
    api_key: str = Depends(get_api_key),
    rate_limit_check: None = Depends(rate_limit)
):
    """
    Search for places near a specific location.

    **Features:**
    - Coordinate-based search center
    - Configurable search radius (100m - 50km)
    - Optional category/query filter
    - Full place details for each result

    **Example:**
    ```json
    {
        "latitude": 40.7128,
        "longitude": -74.0060,
        "radius_meters": 1000,
        "query": "coffee shops"
    }
    ```
    """
    # Caller coordinates are deliberately NOT logged; see analytics.py.
    logger.info("Nearby search radius=%sm", scrub(request.radius_meters))

    try:
        result = await google_maps_service.nearby_search(
            latitude=request.latitude,
            longitude=request.longitude,
            radius_meters=request.radius_meters,
            query=request.query,
            language=request.language,
            max_results=request.max_results,
            timeout=timeout if wait_for_results else None
        )

        if result.get("error"):
            raise upstream_error(result, "Nearby search failed")

        places = result.get("places", [])
        if isinstance(places, list):
            places = google_maps_service.process_place_data(places)

        return {
            "success": True,
            "center": {
                "latitude": request.latitude,
                "longitude": request.longitude
            },
            "radius_meters": request.radius_meters,
            "query": request.query,
            "total_results": len(places),
            "places": places,
            "timestamp": datetime.now().isoformat()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Nearby search error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL)


@router.get(
    "/nearby",
    summary="Search nearby places (GET)",
    response_description="Places within radius"
)
async def nearby_search_get(
    latitude: float = Query(..., ge=-90, le=90, description="Center latitude"),
    longitude: float = Query(..., ge=-180, le=180, description="Center longitude"),
    radius_meters: int = Query(1000, ge=100, le=50000, description="Search radius"),
    query: str | None = Query(None, description="Filter query"),
    language: str = Query("en", description="Language code"),
    max_results: int = Query(20, ge=1, le=100, description="Maximum results"),
    api_key: str = Depends(get_api_key),
    rate_limit_check: None = Depends(rate_limit)
):
    """
    Search for places near a location (GET version).

    Same as POST /nearby but using query parameters.
    """
    request = NearbySearchRequest(
        latitude=latitude,
        longitude=longitude,
        radius_meters=radius_meters,
        query=query,
        language=language,
        max_results=max_results
    )
    return await nearby_search(
        request=request,
        wait_for_results=True,
        timeout=300,
        api_key=api_key,
        rate_limit_check=rate_limit_check
    )


@router.post(
    "/grid-search",
    summary="Grid-based search for comprehensive area coverage",
    response_description="Aggregated results from multiple grid points"
)
async def grid_search(
    request: GridSearchRequest,
    timeout: int = Query(600, ge=60, le=1800, description="Timeout in seconds"),
    api_key: str = Depends(get_api_key),
    rate_limit_check: None = Depends(rate_limit)
):
    """
    Search across a grid of coordinates for comprehensive area coverage.

    Like DataForSEO's calculate_rectangles, this searches multiple viewpoints
    to find ALL businesses in an area, not just those visible from one map view.

    **Grid Size Examples:**
    - 3x3 = 9 search points (fast, basic coverage)
    - 5x5 = 25 search points (balanced, good coverage)
    - 7x7 = 49 search points (thorough coverage)
    - 11x11 = 121 search points (maximum coverage, slower)

    **Use Cases:**
    - Comprehensive business discovery
    - Local SEO rank tracking across an area
    - Market analysis and competitor mapping
    - Finding businesses not visible from a single viewpoint

    **Returns:**
    - Deduplicated places from all grid points
    - Grid metadata showing results count per point
    - grid_positions for each place showing which grid points found it
    """
    try:
        result = await google_maps_service.grid_search(
            query=request.query,
            center_lat=request.center_lat,
            center_lng=request.center_lng,
            radius_km=request.radius_km,
            grid_size=request.grid_size,
            max_results_per_point=request.max_results_per_point,
            language=request.language,
            timeout=timeout
        )

        if result.get("error"):
            raise upstream_error(result, "Grid search failed")

        result["timestamp"] = datetime.now().isoformat()
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Grid search error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL)


@router.post(
    "/bounding-box-search",
    summary="Search within a bounding box",
    response_description="Places within the specified rectangular area"
)
async def bounding_box_search(
    request: BoundingBoxRequest,
    timeout: int = Query(600, ge=60, le=1800, description="Timeout in seconds"),
    api_key: str = Depends(get_api_key),
    rate_limit_check: None = Depends(rate_limit)
):
    """
    Search within a rectangular bounding box defined by corner coordinates.

    Internally creates a grid based on the bounding box dimensions and
    searches each grid point for comprehensive coverage.

    **Use Cases:**
    - Search within a specific neighborhood or district
    - Custom area mapping
    - GIS integration workflows

    **Example bounding box for Gladstone, OR:**
    - north_lat: 45.42, south_lat: 45.35
    - east_lng: -122.55, west_lng: -122.65
    """
    try:
        result = await google_maps_service.bounding_box_search(
            query=request.query,
            north_lat=request.north_lat,
            south_lat=request.south_lat,
            east_lng=request.east_lng,
            west_lng=request.west_lng,
            grid_density=request.grid_density,
            max_results_per_point=request.max_results_per_point,
            language=request.language,
            timeout=timeout
        )

        if result.get("error"):
            raise upstream_error(result, "Bounding box search failed")

        result["bounding_box"] = {
            "north_lat": request.north_lat,
            "south_lat": request.south_lat,
            "east_lng": request.east_lng,
            "west_lng": request.west_lng
        }
        result["timestamp"] = datetime.now().isoformat()
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Bounding box search error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL)


@router.post(
    "/location-search",
    summary="Search by location name",
    response_description="Grid search results with resolved coordinates"
)
async def location_search(
    request: LocationSearchRequest,
    timeout: int = Query(600, ge=60, le=1800, description="Timeout in seconds"),
    api_key: str = Depends(get_api_key),
    rate_limit_check: None = Depends(rate_limit)
):
    """
    Search using a location name instead of coordinates.

    Resolves location names (city, address, ZIP code) to coordinates
    using geocoding, then performs a grid search for comprehensive coverage.

    **Supported Location Formats:**
    - City, State: "Portland, OR", "Seattle, WA"
    - ZIP Code: "97027", "98101"
    - Address: "123 Main St, Portland, OR"
    - Landmark: "Pioneer Courthouse Square"

    **Returns:**
    - resolved_location: Shows what coordinates the location resolved to
    - All grid search results with deduplicated places
    """
    try:
        result = await google_maps_service.location_search(
            query=request.query,
            location=request.location,
            radius_km=request.radius_km,
            grid_size=request.grid_size,
            max_results_per_point=request.max_results_per_point,
            language=request.language,
            timeout=timeout
        )

        if result.get("error"):
            raise upstream_error(result, "Location search failed")

        result["timestamp"] = datetime.now().isoformat()
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Location search error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL)


@router.get(
    "/grid-search",
    summary="Grid-based search (GET)",
    response_description="Aggregated results from grid search"
)
async def grid_search_get(
    query: str = Query(..., min_length=1, max_length=500, description="Search query"),
    center_lat: float = Query(..., ge=-90, le=90, description="Center latitude"),
    center_lng: float = Query(..., ge=-180, le=180, description="Center longitude"),
    radius_km: float = Query(5.0, ge=0.1, le=50, description="Radius in km"),
    grid_size: int = Query(5, ge=3, le=11, description="Grid dimension"),
    max_results_per_point: int = Query(10, ge=1, le=20, description="Results per point"),
    language: str = Query("en", description="Language code"),
    timeout: int = Query(600, ge=60, le=1800, description="Timeout"),
    api_key: str = Depends(get_api_key),
    rate_limit_check: None = Depends(rate_limit)
):
    """GET version of grid search with query parameters."""
    request = GridSearchRequest(
        query=query,
        center_lat=center_lat,
        center_lng=center_lng,
        radius_km=radius_km,
        grid_size=grid_size,
        max_results_per_point=max_results_per_point,
        language=language
    )
    return await grid_search(
        request=request,
        timeout=timeout,
        api_key=api_key,
        rate_limit_check=rate_limit_check
    )


@router.get(
    "/location-search",
    summary="Search by location name (GET)",
    response_description="Grid search results with resolved coordinates"
)
async def location_search_get(
    query: str = Query(..., min_length=1, max_length=500, description="Search query"),
    location: str = Query(..., description="Location name (city, ZIP, address)"),
    radius_km: float = Query(5.0, ge=0.1, le=50, description="Radius in km"),
    grid_size: int = Query(5, ge=3, le=11, description="Grid dimension"),
    max_results_per_point: int = Query(10, ge=1, le=20, description="Results per point"),
    language: str = Query("en", description="Language code"),
    timeout: int = Query(600, ge=60, le=1800, description="Timeout"),
    api_key: str = Depends(get_api_key),
    rate_limit_check: None = Depends(rate_limit)
):
    """GET version of location search with query parameters."""
    request = LocationSearchRequest(
        query=query,
        location=location,
        radius_km=radius_km,
        grid_size=grid_size,
        max_results_per_point=max_results_per_point,
        language=language
    )
    return await location_search(
        request=request,
        timeout=timeout,
        api_key=api_key,
        rate_limit_check=rate_limit_check
    )


@router.get(
    "/autocomplete",
    summary="Place autocomplete",
    response_description="Autocomplete suggestions"
)
async def autocomplete(
    input: str = Query(..., min_length=2, max_length=200, description="Search input"),
    types: str | None = Query(None, description="Place types filter"),
    latitude: float | None = Query(None, ge=-90, le=90, description="Bias latitude"),
    longitude: float | None = Query(None, ge=-180, le=180, description="Bias longitude"),
    radius_meters: int | None = Query(None, ge=1, le=50000, description="Bias radius"),
    language: str = Query("en", description="Language code"),
    api_key: str = Depends(get_api_key),
    rate_limit_check: None = Depends(rate_limit)
):
    """
    Get place autocomplete suggestions.

    **Type Filters:**
    - `establishment` - Businesses
    - `geocode` - Addresses and locations
    - `address` - Street addresses only
    - `(regions)` - Larger areas
    - `(cities)` - Cities only

    **Location Bias:**
    Provide latitude, longitude, and radius to bias results toward a location.

    Returns suggestions with place IDs for further lookup.
    """
    logger.info("Autocomplete: %s", scrub(input))

    try:
        result = await google_maps_service.autocomplete(
            input=input,
            types=types,
            latitude=latitude,
            longitude=longitude,
            radius_meters=radius_meters,
            language=language
        )

        if result.get("error"):
            raise upstream_error(result, "Autocomplete failed")

        return {
            "success": True,
            "input": input,
            "predictions": result.get("predictions", []),
            "timestamp": datetime.now().isoformat()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Autocomplete error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL)


@router.post(
    "/bulk-search",
    summary="Bulk search places",
    response_description="Results for multiple queries"
)
async def bulk_search(
    request: BulkSearchRequest,
    api_key: str = Depends(get_api_key),
    rate_limit_check: None = Depends(rate_limit)
):
    """
    Execute multiple search queries in a single request.

    **Features:**
    - Up to 50 queries per request
    - Parallel execution for faster results
    - Individual error handling per query
    - Combined results in single response

    **Example:**
    ```json
    {
        "queries": [
            "coffee shops in Seattle",
            "restaurants in Portland",
            "hotels in San Francisco"
        ],
        "max_results_per_query": 10
    }
    ```
    """
    logger.info(f"Bulk search: {len(request.queries)} queries")

    try:
        result = await google_maps_service.bulk_search(
            queries=request.queries,
            language=request.language,
            max_results_per_query=request.max_results_per_query
        )

        if result.get("error"):
            raise upstream_error(result, "Bulk search failed")

        return {
            "success": True,
            "total_queries": len(request.queries),
            "successful_queries": result.get("successful_queries", 0),
            "failed_queries": result.get("failed_queries", 0),
            "results": result.get("results", []),
            "timestamp": datetime.now().isoformat()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Bulk search error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL)
