"""
Google Maps Service.

This module provides Google Maps data extraction using native Python
with Playwright for browser automation. No external Docker sidecar required.

Features:
- Business details (name, address, phone, website)
- Ratings and review counts
- Operating hours
- Location coordinates and plus codes
- Category and price level
"""
import logging
import asyncio
import uuid
import math
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime

from app.core.config import get_settings
from app.core.proxy import ENABLE_PROXY, get_proxy, proxy_for

# Every request this service makes is a browser navigation to google.com/maps.
# A plain GET through the datacenter proxy succeeds, but a full Chromium page
# load times out: Google throttles the subresource fetches, so domcontentloaded
# never fires and the job fails after 60s. Direct, the same navigation returns
# in 0.4s. Routing by host lets Maps go direct while Reddit keeps the proxy.
GOOGLE_MAPS_HOST = "https://www.google.com" 
from app.core.log_safety import scrub

logger = logging.getLogger(__name__)


class GoogleMapsService:
    """
    Service class for Google Maps operations using native Playwright scraping.

    This replaces the previous gosom Docker sidecar approach with direct
    browser automation.
    """

    # Seconds to let a Maps page settle after navigation before reading it.
    # Class attributes rather than literals so tests can drop them to zero
    # without patching asyncio.sleep out from under the event loop.
    PAGE_SETTLE_SECONDS = 3
    DIRECTIONS_SETTLE_SECONDS = 4

    def __init__(self):
        """Initialize the service."""
        self._scraper_module = None
        self._initialized = False

    async def _ensure_initialized(self):
        """Lazily initialize the scraper module."""
        if not self._initialized:
            # Import here to avoid circular imports and allow lazy loading
            from app.services import google_maps_scraper
            self._scraper_module = google_maps_scraper
            self._initialized = True

    async def health_check(self) -> Dict[str, Any]:
        """
        Check if the scraping service is healthy.

        Returns:
            Health status dictionary
        """
        try:
            await self._ensure_initialized()

            # For native scraping, we just verify Playwright can be imported
            try:
                from playwright.async_api import async_playwright
                return {
                    "healthy": True,
                    "status_code": 200,
                    "service": "google-maps-native-scraper",
                    "mode": "native-playwright"
                }
            except ImportError:
                return {
                    "healthy": False,
                    "error": "Playwright not installed",
                    "service": "google-maps-native-scraper"
                }

        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return {
                "healthy": False,
                "error": str(e),
                "service": "google-maps-native-scraper"
            }

    async def create_search_job(
        self,
        query: str,
        owner: str = "anonymous",
        language: str = "en",
        max_results: int = 20,
        depth: int = 1,
        email_extraction: bool = False,
        zoom: int = 15,
        geo_coordinates: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a new Google Maps search job.

        The job runs asynchronously in the background.

        Args:
            query: Search query (e.g., "restaurants in New York")
            language: Language code (default: "en")
            max_results: Maximum number of results (default: 20)
            depth: Crawl depth (default: 1) - not used in native mode
            email_extraction: Whether to extract emails - not yet implemented
            zoom: Map zoom level (1-21, default: 15)
            geo_coordinates: Optional geo coordinates for search center

        Returns:
            Job creation response with job_id
        """
        try:
            await self._ensure_initialized()

            # Create job
            job_id = str(uuid.uuid4())
            job_name = f"search_{query[:30].replace(' ', '_')}"

            job = self._scraper_module.ScrapeJob(
                id=job_id,
                name=job_name,
                query=query,
                language=language,
                max_results=max_results,
                zoom=zoom,
                geo_coordinates=geo_coordinates,
                email_extraction=email_extraction,
                owner=owner,
            )

            # Store job
            store = await self._scraper_module.get_job_store()
            await store.create(job)

            # Get proxy if enabled
            proxy = None
            if ENABLE_PROXY:
                proxy = proxy_for(GOOGLE_MAPS_HOST)
                if proxy:
                    logger.info(f"Using proxy for Google Maps scraping")

            # Start background task
            asyncio.create_task(
                self._scraper_module.run_scrape_job(job, proxy=proxy)
            )

            logger.info("Created job %s for query: %s", scrub(job_id), scrub(query))

            return {
                "job_id": job_id,
                "id": job_id,
                "status": "pending",
                "message": "Job created and started"
            }

        except Exception as e:
            logger.error(f"Error creating search job: {e}")
            return {
                "error": True,
                "message": str(e)
            }

    async def get_job_status(self, job_id: str, owner: str) -> Dict[str, Any]:
        """
        Get the status of a scraping job.

        Args:
            job_id: The job ID to check

        Returns:
            Job status information
        """
        try:
            await self._ensure_initialized()

            store = await self._scraper_module.get_job_store()
            # Owner is part of the key: another caller's job is not merely
            # filtered out, it is unreachable, and reports as 404 rather than
            # 403 so job ids cannot be enumerated.
            job = await store.get(owner, job_id)

            if not job:
                return {
                    "error": True,
                    "status_code": 404,
                    "message": "Job not found"
                }

            # Map internal status to expected format
            status_map = {
                "pending": "pending",
                "running": "working",
                "completed": "completed",
                "failed": "failed"
            }

            return {
                "job_id": job.id,
                "status": status_map.get(job.status.value, job.status.value),
                "progress": job.progress,
                "total": job.total,
                "error": job.error,
                "created_at": job.created_at.isoformat(),
                "completed_at": job.completed_at.isoformat() if job.completed_at else None
            }

        except Exception as e:
            logger.error(f"Error getting job status: {e}")
            return {
                "error": True,
                "message": str(e)
            }

    async def get_job_results(
        self,
        job_id: str,
        owner: str,
        format: str = "json"
    ) -> Dict[str, Any]:
        """
        Get the results of a completed job.

        Args:
            job_id: The job ID to get results for
            format: Output format (json, csv)

        Returns:
            Job results with place data
        """
        try:
            await self._ensure_initialized()

            store = await self._scraper_module.get_job_store()
            job = await store.get(owner, job_id)

            if not job:
                return {
                    "error": True,
                    "status_code": 404,
                    "message": "Job not found"
                }

            if job.status != self._scraper_module.JobStatus.COMPLETED:
                return {
                    "error": True,
                    "message": f"Job not completed. Current status: {job.status.value}"
                }

            if format == "csv":
                # Convert to CSV format
                import csv
                import io
                if job.results:
                    output = io.StringIO()
                    writer = csv.DictWriter(output, fieldnames=job.results[0].keys())
                    writer.writeheader()
                    writer.writerows(job.results)
                    return {"data": output.getvalue(), "format": "csv"}
                return {"data": "", "format": "csv"}

            return {
                "results": job.results,
                "format": "json",
                "count": len(job.results),
                "job_id": job_id
            }

        except Exception as e:
            logger.error(f"Error getting job results: {e}")
            return {
                "error": True,
                "message": str(e)
            }

    async def list_jobs(
        self,
        owner: str,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        List all jobs with optional filtering.

        Args:
            status: Filter by job status
            limit: Maximum number of jobs to return
            offset: Pagination offset

        Returns:
            List of jobs
        """
        try:
            await self._ensure_initialized()

            store = await self._scraper_module.get_job_store()
            # list_all() was deliberately removed from the store: it returned
            # every tenant's jobs regardless of caller, which was the
            # vulnerability. Only an owner-scoped listing exists now.
            jobs = await store.list_for_owner(
                owner, status=status, limit=limit, offset=offset
            )

            # Return in gosom-compatible format
            return [job.to_dict() for job in jobs]

        except Exception as e:
            logger.error(f"Error listing jobs: {e}")
            return {
                "error": True,
                "message": str(e)
            }

    async def delete_job(self, job_id: str, owner: str) -> Dict[str, Any]:
        """
        Delete a job and its results.

        Args:
            job_id: The job ID to delete

        Returns:
            Deletion confirmation
        """
        try:
            await self._ensure_initialized()

            store = await self._scraper_module.get_job_store()
            deleted = await store.delete(owner, job_id)

            if deleted:
                return {"success": True, "job_id": job_id}
            else:
                return {
                    "error": True,
                    "status_code": 404,
                    "message": "Job not found"
                }

        except Exception as e:
            logger.error(f"Error deleting job: {e}")
            return {
                "error": True,
                "message": str(e)
            }

    async def search_and_wait(
        self,
        query: str,
        owner: str = "anonymous",
        language: str = "en",
        max_results: int = 20,
        depth: int = 1,
        email_extraction: bool = False,
        zoom: int = 15,
        geo_coordinates: Optional[str] = None,
        timeout: int = 300,
        poll_interval: int = 2
    ) -> Dict[str, Any]:
        """
        Create a search job and wait for results.

        This is a convenience method that creates a job, polls for completion,
        and returns the results.

        Args:
            query: Search query
            language: Language code
            max_results: Maximum results
            depth: Crawl depth (not used in native mode)
            email_extraction: Extract emails from websites
            zoom: Map zoom level
            geo_coordinates: Search center coordinates
            timeout: Maximum wait time in seconds
            poll_interval: Seconds between status checks

        Returns:
            Search results or error
        """
        # Create the job
        job_response = await self.create_search_job(
            query=query,
            owner=owner,
            language=language,
            max_results=max_results,
            depth=depth,
            email_extraction=email_extraction,
            zoom=zoom,
            geo_coordinates=geo_coordinates
        )

        if job_response.get("error"):
            return job_response

        job_id = job_response.get("job_id") or job_response.get("id")
        if not job_id:
            return {
                "error": True,
                "message": "No job_id in response",
                "response": job_response
            }

        # Poll for completion
        elapsed = 0
        while elapsed < timeout:
            status_response = await self.get_job_status(job_id, owner=owner)

            if status_response.get("error"):
                # If it's a real error (not just job not found during creation)
                if status_response.get("status_code") != 404:
                    return status_response

            status = status_response.get("status", "").lower()

            if status == "completed":
                # Get results
                return await self.get_job_results(job_id, owner=owner)
            elif status == "failed":
                return {
                    "error": True,
                    "status": "failed",
                    "job_id": job_id,
                    "details": status_response
                }

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        return {
            "error": True,
            "status": "timeout",
            "job_id": job_id,
            "message": f"Job did not complete within {timeout} seconds"
        }

    def process_place_data(self, raw_data: List[Dict]) -> List[Dict[str, Any]]:
        """
        Process and normalize place data from scraper results.

        Args:
            raw_data: Raw place data from scraper

        Returns:
            Normalized place data
        """
        processed = []

        for place in raw_data:
            processed_place = {
                # Basic info - native scraper uses 'title', 'link', 'cid'
                "place_id": place.get("cid") or place.get("place_id") or place.get("data_id"),
                "name": place.get("title") or place.get("name"),
                "address": place.get("address") or place.get("full_address"),
                "phone": place.get("phone") or place.get("phone_number"),
                "website": place.get("website") or place.get("web"),

                # Location
                "latitude": place.get("latitude") or place.get("lat"),
                "longitude": place.get("longitude") or place.get("lng"),
                "plus_code": place.get("plus_code"),

                # Business info
                "category": place.get("category") or place.get("categories"),
                "rating": place.get("review_rating") or place.get("rating") or place.get("stars"),
                "review_count": place.get("review_count") or place.get("reviews_count") or place.get("reviews"),
                "price_level": place.get("price_range") or place.get("price_level") or place.get("price"),
                "price_per_person": place.get("price_per_person"),

                # Hours
                "hours": place.get("open_hours") or place.get("hours") or place.get("opening_hours") or place.get("working_hours"),
                "is_open_now": place.get("is_open_now") or place.get("open_now"),

                # Additional details
                "description": place.get("description") or place.get("about"),
                "photos": place.get("photos") or place.get("images"),
                "google_maps_url": place.get("link") or place.get("google_maps_url") or place.get("url"),

                # Action links
                "menu_link": place.get("menu_link"),
                "order_link": place.get("order_link"),
                "reserve_link": place.get("reserve_link"),

                # Service options and amenities
                "service_options": place.get("service_options") or [],
                "accessibility": place.get("accessibility") or [],
                "amenities": place.get("amenities") or [],

                # Popular times
                "popular_times": place.get("popular_times") or {},

                # Review details
                "reviews": place.get("reviews_data") or place.get("review_list"),
                "review_summary": place.get("review_summary"),
                "review_topics": place.get("review_topics") or [],
                "sample_reviews": place.get("sample_reviews") or [],

                # Related places
                "related_places": place.get("related_places") or [],

                # Contact info (from email extraction)
                "emails": place.get("emails") or place.get("email"),
                "social_media": {
                    "facebook": place.get("facebook"),
                    "instagram": place.get("instagram"),
                    "twitter": place.get("twitter"),
                    "linkedin": place.get("linkedin"),
                    "youtube": place.get("youtube")
                }
            }

            # Clean up None values in social_media
            processed_place["social_media"] = {
                k: v for k, v in processed_place["social_media"].items() if v
            } or None

            # Clean up empty lists/dicts
            for key in ["service_options", "accessibility", "amenities", "review_topics", "sample_reviews", "related_places"]:
                if not processed_place.get(key):
                    processed_place[key] = None
            if not processed_place.get("popular_times"):
                processed_place["popular_times"] = None

            processed.append(processed_place)

        return processed


    # =========================================================================
    # Extended Feature Methods
    # =========================================================================

    async def get_place_by_id(self, place_id: str) -> Dict[str, Any]:
        """
        Get place details by Place ID.

        Args:
            place_id: Google Place ID (CID or ChIJ format)

        Returns:
            Place details or error
        """
        try:
            await self._ensure_initialized()

            # Construct URL from place_id
            if place_id.startswith("0x"):
                # CID format - use data parameter
                url = f"https://www.google.com/maps/place/?q=place_id:{place_id}"
            else:
                # ChIJ format
                url = f"https://www.google.com/maps/place/?q=place_id:{place_id}"

            return await self.lookup_place(url=url)

        except Exception as e:
            logger.error(f"Error getting place by ID: {e}")
            return {"error": True, "message": str(e)}

    async def lookup_place(
        self,
        url: Optional[str] = None,
        place_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Look up a place by URL or Place ID.

        Args:
            url: Google Maps URL
            place_id: Google Place ID

        Returns:
            Place details or error
        """
        try:
            await self._ensure_initialized()

            if place_id and not url:
                return await self.get_place_by_id(place_id)

            if not url:
                return {"error": True, "message": "URL or place_id required"}

            # Create a scraper and extract place details
            from app.services.google_maps_scraper import GoogleMapsScraper
            from app.core.proxy import ENABLE_PROXY, proxy_for

            proxy = None
            if ENABLE_PROXY:
                proxy = proxy_for(GOOGLE_MAPS_HOST)

            scraper = GoogleMapsScraper(proxy=proxy, headless=True)
            try:
                page, context = await scraper._create_page("en")

                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(3)

                place_data = await scraper._extract_place_details(page)

                await context.close()

                if place_data:
                    processed = self.process_place_data([place_data])
                    return {"place": processed[0] if processed else None}
                else:
                    return {"error": True, "status_code": 404, "message": "Place not found"}

            finally:
                await scraper.close()

        except Exception as e:
            logger.error(f"Error looking up place: {e}")
            return {"error": True, "message": str(e)}

    async def nearby_search(
        self,
        latitude: float,
        longitude: float,
        radius_meters: int = 1000,
        query: Optional[str] = None,
        language: str = "en",
        max_results: int = 20,
        timeout: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Search for places near a location.

        Args:
            latitude: Center latitude
            longitude: Center longitude
            radius_meters: Search radius in meters
            query: Optional filter query
            language: Language code
            max_results: Maximum results
            timeout: Optional timeout

        Returns:
            Search results or error
        """
        try:
            await self._ensure_initialized()

            # Build search query with location
            search_query = query if query else "places"
            geo_coords = f"{latitude},{longitude}"

            # Use existing search with coordinates
            result = await self.search_and_wait(
                query=search_query,
                language=language,
                max_results=max_results,
                geo_coordinates=geo_coords,
                zoom=self._radius_to_zoom(radius_meters),
                timeout=timeout or 300
            )

            if result.get("error"):
                return result

            return {
                "places": result.get("results", []),
                "center": {"latitude": latitude, "longitude": longitude}
            }

        except Exception as e:
            logger.error(f"Nearby search error: {e}")
            return {"error": True, "message": str(e)}

    def _radius_to_zoom(self, radius_meters: int) -> int:
        """Convert radius in meters to appropriate zoom level."""
        if radius_meters <= 500:
            return 17
        elif radius_meters <= 1000:
            return 16
        elif radius_meters <= 2000:
            return 15
        elif radius_meters <= 5000:
            return 14
        elif radius_meters <= 10000:
            return 13
        elif radius_meters <= 20000:
            return 12
        elif radius_meters <= 50000:
            return 11
        else:
            return 10

    def _calculate_grid_coordinates(
        self,
        center_lat: float,
        center_lng: float,
        radius_km: float,
        grid_size: int = 5
    ) -> List[Tuple[float, float]]:
        """
        Generate a grid of coordinates around a center point.

        This enables DataForSEO-style grid-based search for comprehensive
        area coverage, finding all businesses not just those visible from
        a single viewpoint.

        Args:
            center_lat: Center latitude
            center_lng: Center longitude
            radius_km: Radius in kilometers (distance from center to edge)
            grid_size: Number of points per side (e.g., 5 for 5x5 = 25 points)

        Returns:
            List of (lat, lng) tuples
        """
        # Calculate the step size between grid points
        step_km = (radius_km * 2) / (grid_size - 1) if grid_size > 1 else 0

        # Convert km to degrees (approximate)
        # 1 degree lat = 111.32 km
        lat_step = step_km / 111.32
        lng_step = step_km / (111.32 * math.cos(math.radians(center_lat)))

        coordinates = []

        # Calculate starting point (top-left corner)
        start_lat = center_lat + (radius_km / 111.32)
        start_lng = center_lng - (radius_km / (111.32 * math.cos(math.radians(center_lat))))

        for row in range(grid_size):
            for col in range(grid_size):
                lat = start_lat - (row * lat_step)
                lng = start_lng + (col * lng_step)
                coordinates.append((round(lat, 7), round(lng, 7)))

        return coordinates

    async def grid_search(
        self,
        query: str,
        center_lat: float,
        center_lng: float,
        radius_km: float = 5.0,
        grid_size: int = 5,
        max_results_per_point: int = 10,
        language: str = "en",
        timeout: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Search across a grid of coordinates for comprehensive area coverage.

        Like DataForSEO's calculate_rectangles, this searches multiple viewpoints
        to find ALL businesses in an area, not just those visible from one map view.

        Args:
            query: Search query (e.g., "restaurants")
            center_lat: Center latitude
            center_lng: Center longitude
            radius_km: Search radius in km (default 5km)
            grid_size: Grid dimension (5 = 5x5 = 25 points, max 11x11 = 121)
            max_results_per_point: Max results per grid point
            language: Language code
            timeout: Optional timeout in seconds

        Returns:
            Aggregated results with grid metadata and deduplicated places
        """
        try:
            await self._ensure_initialized()

            # Validate grid size
            grid_size = min(max(grid_size, 3), 11)  # 3x3 to 11x11

            grid_coords = self._calculate_grid_coordinates(
                center_lat, center_lng, radius_km, grid_size
            )

            all_results = {}
            grid_data = []
            failed_points = 0

            logger.info(
                    "Starting grid search: %s with %d grid points",
                    scrub(query), len(grid_coords),
                )

            for idx, (lat, lng) in enumerate(grid_coords):
                try:
                    # Use higher zoom for more focused local results
                    zoom = 16 if radius_km <= 2 else 15

                    result = await self.search_and_wait(
                        query=query,
                        language=language,
                        max_results=max_results_per_point,
                        geo_coordinates=f"{lat},{lng}",
                        zoom=zoom,
                        timeout=timeout or 60
                    )

                    results_count = 0
                    point = {
                        "grid_index": idx,
                        "lat": lat,
                        "lng": lng,
                        "results_count": 0,
                    }

                    if result.get("error"):
                        # A failing point used to leave results_count at 0 with
                        # nothing recorded, so a grid where every point failed
                        # was indistinguishable from a grid that genuinely
                        # found nothing.
                        failed_points += 1
                        point["error"] = "search failed for this grid point"
                        logger.warning(
                            "Grid point %s (%s, %s) returned an error: %s",
                            idx, lat, lng, result.get("message", "unknown"),
                        )
                    else:
                        places = result.get("results", [])
                        results_count = len(places)
                        point["results_count"] = results_count

                        # Dedupe by place_id
                        for place in places:
                            place_id = place.get("place_id")
                            if place_id and place_id not in all_results:
                                place["grid_positions"] = [idx]
                                all_results[place_id] = place
                            elif place_id:
                                all_results[place_id]["grid_positions"].append(idx)

                    grid_data.append(point)

                except Exception as e:
                    failed_points += 1
                    logger.warning(f"Grid point {idx} ({lat}, {lng}) failed: {e}")
                    grid_data.append({
                        "grid_index": idx,
                        "lat": lat,
                        "lng": lng,
                        "results_count": 0,
                        "error": str(e)
                    })

            # Every point failing is an outage, not an empty neighbourhood.
            # Returning success with places: [] here is the same class of bug
            # as the fabricated Maps endpoints: a total failure that looks like
            # a valid negative answer.
            if grid_coords and failed_points == len(grid_coords):
                return {
                    "error": True,
                    "status_code": 502,
                    "message": (
                        f"All {failed_points} grid points failed; no result is "
                        "available for this area."
                    ),
                    "grid_metadata": grid_data,
                }

            return {
                "success": True,
                "partial": failed_points > 0,
                "failed_grid_points": failed_points,
                "query": query,
                "center": {"lat": center_lat, "lng": center_lng},
                "radius_km": radius_km,
                "grid_size": grid_size,
                "total_grid_points": len(grid_coords),
                "unique_places": len(all_results),
                "grid_metadata": grid_data,
                "places": list(all_results.values())
            }

        except Exception as e:
            logger.error(f"Grid search error: {e}")
            return {"error": True, "message": str(e)}

    async def bounding_box_search(
        self,
        query: str,
        north_lat: float,
        south_lat: float,
        east_lng: float,
        west_lng: float,
        grid_density: int = 5,
        max_results_per_point: int = 10,
        language: str = "en",
        timeout: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Search within a bounding box by creating a grid.

        Args:
            query: Search query
            north_lat: Top boundary (max latitude)
            south_lat: Bottom boundary (min latitude)
            east_lng: Right boundary (max longitude)
            west_lng: Left boundary (min longitude)
            grid_density: Points per side for grid
            max_results_per_point: Max results per grid point
            language: Language code
            timeout: Optional timeout

        Returns:
            Aggregated results with grid metadata
        """
        try:
            # Calculate center and radius
            center_lat = (north_lat + south_lat) / 2
            center_lng = (east_lng + west_lng) / 2

            # Calculate radius from center to corner (in km)
            lat_diff = abs(north_lat - south_lat) / 2
            lng_diff = abs(east_lng - west_lng) / 2

            # Convert to km (approximate)
            lat_km = lat_diff * 111.32
            lng_km = lng_diff * 111.32 * math.cos(math.radians(center_lat))
            radius_km = max(lat_km, lng_km)

            return await self.grid_search(
                query=query,
                center_lat=center_lat,
                center_lng=center_lng,
                radius_km=radius_km,
                grid_size=grid_density,
                max_results_per_point=max_results_per_point,
                language=language,
                timeout=timeout
            )

        except Exception as e:
            logger.error(f"Bounding box search error: {e}")
            return {"error": True, "message": str(e)}

    async def location_search(
        self,
        query: str,
        location: str,
        radius_km: float = 5.0,
        grid_size: int = 5,
        max_results_per_point: int = 10,
        language: str = "en",
        timeout: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Search using a location name instead of coordinates.

        Resolves location names (city, address, ZIP) to coordinates
        using geocoding, then performs a grid search.

        Args:
            query: Search query (e.g., "restaurants")
            location: Location name (e.g., "Portland, OR", "97027", "123 Main St")
            radius_km: Search radius in km
            grid_size: Grid dimension for comprehensive coverage
            max_results_per_point: Max results per grid point
            language: Language code
            timeout: Optional timeout

        Returns:
            Aggregated results with grid metadata
        """
        try:
            await self._ensure_initialized()

            # First, geocode the location
            geocode_result = await self.geocode(location, language=language)

            if geocode_result.get("error"):
                return geocode_result

            coords = geocode_result.get("coordinates", {})
            lat = coords.get("latitude")
            lng = coords.get("longitude")

            if not lat or not lng:
                return {"error": True, "message": f"Could not resolve location: {location}"}

            # Perform grid search with resolved coordinates
            result = await self.grid_search(
                query=query,
                center_lat=float(lat),
                center_lng=float(lng),
                radius_km=radius_km,
                grid_size=grid_size,
                max_results_per_point=max_results_per_point,
                language=language,
                timeout=timeout
            )

            # Add location resolution info to result
            if not result.get("error"):
                result["resolved_location"] = {
                    "input": location,
                    "resolved_address": geocode_result.get("address"),
                    "latitude": lat,
                    "longitude": lng
                }

            return result

        except Exception as e:
            logger.error(f"Location search error: {e}")
            return {"error": True, "message": str(e)}

    async def get_place_reviews(
        self,
        place_id: str,
        sort_by: str = "most_relevant",
        limit: int = 50,
        offset: int = 0,
        min_rating: Optional[int] = None,
        include_owner_responses: bool = True
    ) -> Dict[str, Any]:
        """
        Get the sample of reviews rendered on the place panel.

        The panel renders a sample, not the full review set, so ``limit``,
        ``offset`` and ``min_rating`` are applied *to that sample* and
        ``filters_applied`` says exactly which of the requested parameters
        actually took effect. ``sort_by`` and ``include_owner_responses``
        cannot be applied -- the panel's order is Google's and owner responses
        are not separable from the review text we scrape -- so they are
        reported as not applied rather than silently ignored, which would let a
        caller believe they received a sorted, filtered page.
        """
        try:
            await self._ensure_initialized()

            # Get place data first
            place_result = await self.get_place_by_id(place_id)

            if place_result.get("error"):
                return place_result

            place = place_result.get("place", {})

            # None, not 0, when Google did not render a count: "we could not
            # read the total" is not "this place has no reviews".
            total_reviews = place.get("review_count")
            sample = place.get("reviews") or []

            if min_rating is not None:
                sample = [
                    r for r in sample
                    if isinstance(r, dict) and (r.get("rating") or 0) >= min_rating
                ]
            sample_size_before_paging = len(sample)
            sample = sample[offset : offset + limit]

            return {
                "total_reviews": total_reviews,
                "average_rating": place.get("rating"),
                "reviews": sample,
                "filters_applied": {
                    "limit": True,
                    "offset": True,
                    "min_rating": min_rating is not None,
                    # Named explicitly so the caller knows these did nothing.
                    "sort_by": False,
                    "include_owner_responses": False,
                },
                "sample_size": sample_size_before_paging,
                "review_summary": place.get("review_summary"),
                "review_topics": place.get("review_topics"),
                # Honest: the place panel renders only a sample of reviews, so
                # "has_more" is whether the sample is smaller than the count
                # Google reports -- not a flat False that claims we returned
                # every review. None where the count is unknown, because then
                # we genuinely cannot tell.
                "has_more": (
                    None if total_reviews is None
                    else (offset + len(sample)) < min(total_reviews, sample_size_before_paging)
                    or sample_size_before_paging < total_reviews
                ),
                "message": (
                    "Reviews are the sample rendered on the place panel, not the full "
                    "review set. limit/offset/min_rating were applied to that sample; "
                    "sort_by and include_owner_responses could not be applied. See "
                    "filters_applied."
                ),
            }

        except Exception as e:
            logger.error(f"Get reviews error: {e}")
            return {"error": True, "message": str(e)}

    async def get_place_photos(
        self,
        place_id: str,
        max_photos: int = 20,
        size: str = "large",
        category: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get photos for a place.

        Returns photos extracted during place scraping.
        """
        try:
            await self._ensure_initialized()

            # Get place data
            place_result = await self.get_place_by_id(place_id)

            if place_result.get("error"):
                return place_result

            place = place_result.get("place", {})
            photos = place.get("photos") or []

            # Apply size transformation
            size_map = {
                "thumbnail": "=w100-h100",
                "medium": "=w400-h300",
                "large": "=w800-h600",
                "original": "=w0"
            }
            size_suffix = size_map.get(size, "=w800-h600")

            sized_photos = []
            for photo_url in photos[:max_photos]:
                if "googleusercontent.com" in photo_url:
                    # Replace size in URL
                    import re
                    new_url = re.sub(r'=w\d+-h\d+', size_suffix, photo_url)
                    sized_photos.append({"url": new_url})
                else:
                    sized_photos.append({"url": photo_url})

            return {
                "total_photos": len(photos),
                "photos": sized_photos
            }

        except Exception as e:
            logger.error(f"Get photos error: {e}")
            return {"error": True, "message": str(e)}

    # Candidate selectors for the Q&A block, tried in order. A miss on all of
    # them is reported as "not rendered", never as "no questions".
    _QA_SECTION_SELECTORS = (
        'div[aria-label*="Questions and answers"]',
        'div[jsaction*="questions"]',
    )
    _QA_ITEM_SELECTORS = (
        'div[aria-label*="Questions and answers"] div[role="listitem"]',
        'div[jsaction*="pane.question"]',
    )
    _QA_ANSWER_SELECTORS = (
        'div[jsaction*="pane.answer"]',
        'div[role="listitem"] div[role="listitem"]',
    )

    async def _extract_place_qa(
        self,
        page,
        limit: int,
        include_answers: bool
    ) -> Optional[List[Dict[str, Any]]]:
        """Read the Q&A entries on a place page.

        Returns None when no Q&A section was rendered at all, which is
        different from ``[]`` (a section with no questions in it).
        """
        section_present = False
        for selector in self._QA_SECTION_SELECTORS:
            if await page.query_selector(selector):
                section_present = True
                break

        nodes = []
        for selector in self._QA_ITEM_SELECTORS:
            nodes = await page.query_selector_all(selector)
            if nodes:
                break

        if not nodes and not section_present:
            return None

        questions: List[Dict[str, Any]] = []
        for node in nodes[:limit]:
            text = " ".join(((await node.inner_text()) or "").split())
            if not text:
                continue
            entry: Dict[str, Any] = {"question": text}
            if include_answers:
                answers: List[str] = []
                for selector in self._QA_ANSWER_SELECTORS:
                    answer_nodes = await node.query_selector_all(selector)
                    if answer_nodes:
                        for answer_node in answer_nodes:
                            answer_text = " ".join(((await answer_node.inner_text()) or "").split())
                            if answer_text:
                                answers.append(answer_text)
                        break
                entry["answers"] = answers
            questions.append(entry)
        return questions

    async def get_place_qa(
        self,
        place_id: str,
        limit: int = 20,
        include_answers: bool = True
    ) -> Dict[str, Any]:
        """
        Get Q&A for a place by scraping the questions rendered on its page.

        This previously returned ``total_questions: 0, questions: []`` for
        every place without looking at anything -- the same
        empty-success-as-fact bug as the monitor and menu endpoints. The
        outcomes are now distinct in ``qa_status``:

        - ``scraped`` -- questions were rendered and read.
        - ``no_questions`` -- a Q&A section was found and no questions were
          read from it. The nearest thing to a real "nobody has asked
          anything" this page supports.
        - ``not_rendered`` -- Google rendered no Q&A section for this place, so
          nothing is known either way. Not the same as "no questions".

        Args:
            place_id: Place whose Q&A is wanted.
            limit: Maximum questions to return.
            include_answers: Include each question's answers.
        """
        await self._ensure_initialized()

        place_result = await self.get_place_by_id(place_id)
        if place_result.get("error"):
            return place_result

        place = place_result.get("place") or {}
        place_url = place.get("google_maps_url") or f"https://www.google.com/maps/place/?q=place_id:{place_id}"

        from app.services.google_maps_scraper import GoogleMapsScraper

        proxy = None
        if ENABLE_PROXY:
            proxy = proxy_for(GOOGLE_MAPS_HOST)

        scraper = GoogleMapsScraper(proxy=proxy, headless=True)
        try:
            page, context = await scraper._create_page("en")
            try:
                await page.goto(place_url, wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(self.PAGE_SETTLE_SECONDS)
                questions = await self._extract_place_qa(page, limit, include_answers)
            finally:
                await context.close()
        except Exception as e:
            logger.error(
                    "Q&A scrape failed for %s: %s", scrub(place_id), scrub(e),
                    exc_info=True,
                )
            return {
                "error": True,
                "status_code": 502,
                "message": f"Could not load the place page to read its Q&A: {e}",
            }
        finally:
            await scraper.close()

        if questions is None:
            return {
                # None, not 0: a count of zero alongside "we could not see the
                # section" contradicts itself, and 0 is the half a client reads.
                "total_questions": None,
                "questions": [],
                "qa_status": "not_rendered",
                "message": (
                    "No questions-and-answers section was found on this place's page, "
                    "so it is unknown whether any questions exist. This is not a count "
                    "of zero."
                ),
            }

        return {
            "total_questions": len(questions),
            "questions": questions,
            "qa_status": "scraped" if questions else "no_questions",
            "source": "google_maps_scrape",
        }

    async def autocomplete(
        self,
        input: str,
        types: Optional[str] = None,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        radius_meters: Optional[int] = None,
        language: str = "en"
    ) -> Dict[str, Any]:
        """
        Get place autocomplete suggestions.

        Note: This would require integration with Google's autocomplete
        or scraping the autocomplete dropdown.
        """
        try:
            await self._ensure_initialized()

            # Perform a quick search and return top results as suggestions
            result = await self.search_and_wait(
                query=input,
                language=language,
                max_results=5,
                geo_coordinates=f"{latitude},{longitude}" if latitude and longitude else None,
                timeout=30
            )

            if result.get("error"):
                return result

            predictions = []
            for place in result.get("results", [])[:5]:
                predictions.append({
                    "description": f"{place.get('title', '')} - {place.get('address', '')}",
                    "place_id": place.get("cid"),
                    "main_text": place.get("title", ""),
                    "secondary_text": place.get("address", "")
                })

            return {"predictions": predictions}

        except Exception as e:
            logger.error(f"Autocomplete error: {e}")
            return {"error": True, "message": str(e)}

    async def bulk_search(
        self,
        queries: List[str],
        language: str = "en",
        max_results_per_query: int = 10
    ) -> Dict[str, Any]:
        """
        Execute multiple search queries.

        Args:
            queries: List of search queries
            language: Language code
            max_results_per_query: Max results per query

        Returns:
            Combined results
        """
        try:
            await self._ensure_initialized()

            results = []
            successful = 0
            failed = 0

            for query in queries:
                try:
                    result = await self.search_and_wait(
                        query=query,
                        language=language,
                        max_results=max_results_per_query,
                        timeout=120
                    )

                    if result.get("error"):
                        failed += 1
                        results.append({
                            "query": query,
                            "success": False,
                            "error": result.get("message"),
                            "places": []
                        })
                    else:
                        successful += 1
                        places = result.get("results", [])
                        results.append({
                            "query": query,
                            "success": True,
                            "count": len(places),
                            "places": self.process_place_data(places)
                        })

                except Exception as e:
                    failed += 1
                    results.append({
                        "query": query,
                        "success": False,
                        "error": str(e),
                        "places": []
                    })

            return {
                "results": results,
                "successful_queries": successful,
                "failed_queries": failed
            }

        except Exception as e:
            logger.error(f"Bulk search error: {e}")
            return {"error": True, "message": str(e)}

    async def get_review_analytics(
        self,
        place_id: str,
        time_period: str = "all",
        include_sentiment: bool = True,
        include_trends: bool = True,
        include_keywords: bool = True
    ) -> Dict[str, Any]:
        """
        Get analytics for a place's reviews.

        Returns analysis based on available review data.
        """
        try:
            await self._ensure_initialized()

            # Get place data
            place_result = await self.get_place_by_id(place_id)

            if place_result.get("error"):
                return place_result

            place = place_result.get("place", {})

            analytics = {
                "rating_distribution": place.get("review_summary") or {},
                "average_rating": place.get("rating"),
                "total_reviews": place.get("review_count"),
                "keywords": place.get("review_topics") or []
            }

            if include_sentiment:
                # Simple sentiment based on rating
                rating = place.get("rating") or 0
                if rating >= 4.0:
                    analytics["overall_sentiment"] = "positive"
                elif rating >= 3.0:
                    analytics["overall_sentiment"] = "neutral"
                else:
                    analytics["overall_sentiment"] = "negative"

            return {"analytics": analytics}

        except Exception as e:
            logger.error(f"Analytics error: {e}")
            return {"error": True, "message": str(e)}

    async def analyze_competitors(
        self,
        latitude: float,
        longitude: float,
        category: str,
        radius_meters: int = 2000,
        max_competitors: int = 10
    ) -> Dict[str, Any]:
        """
        Find and analyze competitors in an area.
        """
        try:
            await self._ensure_initialized()

            # Search for businesses in the category
            result = await self.nearby_search(
                latitude=latitude,
                longitude=longitude,
                radius_meters=radius_meters,
                query=category,
                max_results=max_competitors
            )

            if result.get("error"):
                return result

            places = result.get("places", [])
            processed = self.process_place_data(places) if places else []

            # Calculate summary statistics
            ratings = [float(p.get("rating")) for p in processed if p.get("rating")]
            review_counts = []
            for p in processed:
                rc = p.get("review_count")
                if rc:
                    try:
                        review_counts.append(int(str(rc).replace(",", "")))
                    except (ValueError, TypeError):
                        pass

            def get_rating(x):
                try:
                    return float(x.get("rating") or 0)
                except (ValueError, TypeError):
                    return 0

            def get_review_count(x):
                try:
                    return int(str(x.get("review_count") or 0).replace(",", ""))
                except (ValueError, TypeError):
                    return 0

            summary = {
                "total_competitors": len(processed),
                "average_rating": sum(ratings) / len(ratings) if ratings else None,
                "total_reviews": sum(review_counts) if review_counts else 0,
                "highest_rated": max(processed, key=get_rating).get("name") if processed else None,
                "most_reviewed": max(processed, key=get_review_count).get("name") if processed else None
            }

            return {
                "competitors": processed,
                "summary": summary
            }

        except Exception as e:
            logger.error(f"Competitor analysis error: {e}")
            return {"error": True, "message": str(e)}

    # =========================================================================
    # Monitors and webhooks
    #
    # These delegate to app.services.google_maps_monitors, which owns the
    # durable owner-scoped storage and the background scheduler. The service
    # layer only translates between the API's api_key and the store's owner id,
    # and between exceptions and the error dicts the router expects.
    # =========================================================================

    @staticmethod
    def _owner(api_key: Optional[str]) -> str:
        """Map an API key to the storage owner id."""
        from app.services.google_maps_monitors import owner_id_for_api_key

        return owner_id_for_api_key(api_key)

    async def create_monitor(
        self,
        place_id: Optional[str] = None,
        url: Optional[str] = None,
        webhook_url: Optional[str] = None,
        check_interval_hours: int = 24,
        track_fields: List[str] = None,
        api_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a monitor for a place.

        The monitor is persisted and scheduled before this returns, so the
        reported ``status`` describes durable state: a subsequent
        :meth:`get_monitor` on the returned id finds it. Previously this
        returned ``status: "active"`` for a monitor that was never stored.

        Args:
            place_id: Place to monitor (one of place_id/url required).
            url: Google Maps URL to monitor.
            webhook_url: Optional notification target, SSRF-validated.
            check_interval_hours: Hours between checks.
            track_fields: Fields to watch; defaults to a standard set.
            api_key: Caller's API key; scopes the monitor to that owner.

        Returns:
            The stored monitor, or an error dict.
        """
        from app.services import google_maps_monitors as monitors

        try:
            return await monitors.create_monitor(
                owner=self._owner(api_key),
                place_id=place_id,
                url=url,
                webhook_url=webhook_url,
                check_interval_hours=check_interval_hours,
                track_fields=track_fields,
            )
        except monitors.InvalidWebhookTarget as e:
            logger.warning(f"Monitor rejected: {e.reason}")
            return {"error": True, "status_code": 400, "message": e.public_message}
        except ValueError as e:
            return {"error": True, "status_code": 400, "message": str(e)}

    async def list_monitors(
        self,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        api_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """List this caller's monitors. Never returns another caller's."""
        from app.services import google_maps_monitors as monitors

        return await monitors.list_monitors(
            owner=self._owner(api_key), status=status, limit=limit, offset=offset
        )

    async def get_monitor(
        self,
        monitor_id: str,
        include_history: bool = True,
        api_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get one monitor, including its recorded snapshot history."""
        from app.services import google_maps_monitors as monitors

        try:
            monitor = await monitors.get_monitor(
                owner=self._owner(api_key),
                monitor_id=monitor_id,
                include_history=include_history,
            )
        except monitors.MonitorNotFound:
            return {"error": True, "status_code": 404, "message": "Monitor not found"}
        return {"monitor": monitor}

    async def delete_monitor(
        self,
        monitor_id: str,
        api_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """Delete one of this caller's monitors."""
        from app.services import google_maps_monitors as monitors

        try:
            await monitors.delete_monitor(owner=self._owner(api_key), monitor_id=monitor_id)
        except monitors.MonitorNotFound:
            return {"error": True, "status_code": 404, "message": "Monitor not found"}
        return {"deleted": True, "monitor_id": monitor_id}

    async def check_monitor_now(
        self,
        monitor_id: str,
        api_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """Run one monitor check immediately, outside the schedule."""
        from app.services import google_maps_monitors as monitors

        try:
            return await monitors.check_monitor(
                owner=self._owner(api_key), monitor_id=monitor_id
            )
        except monitors.MonitorNotFound:
            return {"error": True, "status_code": 404, "message": "Monitor not found"}

    async def register_webhook(
        self,
        url: str,
        events: List[str],
        secret: Optional[str] = None,
        api_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Register a webhook that is actually delivered to.

        The target is SSRF-validated here and again before every delivery.
        Deliveries are HMAC-SHA256 signed with the returned secret, which is
        shown once and never echoed by :meth:`list_webhooks`.
        """
        from app.services import google_maps_monitors as monitors

        try:
            return await monitors.register_webhook(
                owner=self._owner(api_key), url=url, events=events, secret=secret
            )
        except monitors.InvalidWebhookTarget as e:
            logger.warning(f"Webhook target rejected: {e.reason}")
            return {"error": True, "status_code": 400, "message": e.public_message}
        except ValueError as e:
            return {"error": True, "status_code": 400, "message": str(e)}

    async def list_webhooks(
        self,
        limit: int = 50,
        offset: int = 0,
        api_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """List this caller's webhooks with their real delivery counters."""
        from app.services import google_maps_monitors as monitors

        return await monitors.list_webhooks(
            owner=self._owner(api_key), limit=limit, offset=offset
        )

    async def delete_webhook(
        self,
        webhook_id: str,
        api_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """Delete one of this caller's webhooks."""
        from app.services import google_maps_monitors as monitors

        try:
            await monitors.delete_webhook(owner=self._owner(api_key), webhook_id=webhook_id)
        except monitors.WebhookNotFound:
            return {"error": True, "status_code": 404, "message": "Webhook not found"}
        return {"deleted": True, "webhook_id": webhook_id}

    # =========================================================================
    # Directions
    # =========================================================================

    # Travel modes Google Maps accepts in the `travelmode` URL parameter.
    TRAVEL_MODES = ("driving", "walking", "bicycling", "transit")

    # Candidate selectors for a route card in the directions pane, tried in
    # order. Google rewrites its class names regularly, so relying on a single
    # selector guarantees a silent breakage; a miss on all of them is reported
    # as a scrape failure rather than papered over.
    _ROUTE_SELECTORS = (
        'div[id^="section-directions-trip-"]',
        "div[data-trip-index]",
        'div[role="radiogroup"] > div[role="radio"]',
    )

    _STEP_SELECTORS = (
        'div[class*="directions-mode-step"]',
        'div[jsaction*="directions.step"]',
        'div[id^="section-directions-trip-"] div[role="listitem"]',
    )

    # Metres per unit, for normalising whatever unit Google renders.
    _DISTANCE_UNITS = {
        "km": 1000.0,
        "m": 1.0,
        "mi": 1609.344,
        "ft": 0.3048,
    }

    @staticmethod
    def _format_duration(seconds: Optional[int]) -> Optional[str]:
        """Render a duration in seconds as '1 hr 24 min'."""
        if not seconds:
            return None
        hours, remainder = divmod(int(seconds), 3600)
        minutes = remainder // 60
        if hours and minutes:
            return f"{hours} hr {minutes} min"
        if hours:
            return f"{hours} hr"
        return f"{minutes} min"

    @staticmethod
    def _parse_duration(text: str) -> Optional[int]:
        """Parse '1 hr 24 min' / '35 min' / '2 h' into seconds, or None."""
        import re

        hours = re.search(r"(\d+)\s*(?:hours?|hrs?|h)\b", text, re.IGNORECASE)
        minutes = re.search(r"(\d+)\s*(?:minutes?|mins?|min)\b", text, re.IGNORECASE)
        days = re.search(r"(\d+)\s*(?:days?|d)\b", text, re.IGNORECASE)
        if not (hours or minutes or days):
            return None
        total = 0
        if days:
            total += int(days.group(1)) * 86400
        if hours:
            total += int(hours.group(1)) * 3600
        if minutes:
            total += int(minutes.group(1)) * 60
        return total or None

    @classmethod
    def _parse_distance(cls, text: str) -> Optional[Tuple[str, float]]:
        """Parse '12.4 km' / '850 m' / '3.1 mi' into (label, metres), or None."""
        import re

        match = re.search(
            r"(\d[\d,]*(?:\.\d+)?)\s*(km|mi|ft|m)\b",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        try:
            value = float(match.group(1).replace(",", ""))
        except ValueError:
            return None
        unit = match.group(2).lower()
        factor = cls._DISTANCE_UNITS.get(unit)
        if factor is None:
            return None
        return match.group(0).strip(), value * factor

    @classmethod
    def _parse_route_card(cls, text: str) -> Optional[Dict[str, Any]]:
        """Turn a route card's rendered text into a route, or None if it is not one.

        Returns None when neither a duration nor a distance is present, which
        is how a non-route element that happened to match a selector is
        rejected instead of being emitted as a route with null fields.
        """
        cleaned = " ".join(text.split())
        if not cleaned:
            return None

        duration_seconds = cls._parse_duration(cleaned)
        distance = cls._parse_distance(cleaned)
        if duration_seconds is None and distance is None:
            return None

        # The summary is the "via ..." line Google renders for each route.
        summary = None
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("via "):
                summary = stripped
                break

        return {
            "summary": summary,
            "distance": distance[0] if distance else None,
            "distance_meters": round(distance[1]) if distance else None,
            "duration": cls._format_duration(duration_seconds),
            "duration_seconds": duration_seconds,
        }

    async def _extract_direction_routes(self, page, max_routes: int) -> List[Dict[str, Any]]:
        """Read the route cards Google rendered. Empty list means none were found."""
        nodes = []
        for selector in self._ROUTE_SELECTORS:
            nodes = await page.query_selector_all(selector)
            if nodes:
                break

        routes: List[Dict[str, Any]] = []
        for node in nodes[:max_routes]:
            text = await node.inner_text()
            parsed = self._parse_route_card(text or "")
            if parsed is not None:
                routes.append(parsed)
        return routes

    async def _extract_direction_steps(self, page) -> Optional[List[Dict[str, Any]]]:
        """Read the turn-by-turn steps, or None if Google did not render any.

        None and ``[]`` mean different things and are kept apart: None is "the
        step list was not present on the page", which the caller reports as
        ``steps_available: false`` rather than as a route with no turns.
        """
        nodes = []
        for selector in self._STEP_SELECTORS:
            nodes = await page.query_selector_all(selector)
            if nodes:
                break
        if not nodes:
            return None

        steps: List[Dict[str, Any]] = []
        for node in nodes:
            text = (await node.inner_text()) or ""
            instruction = " ".join(text.split())
            if not instruction:
                continue
            distance = self._parse_distance(instruction)
            steps.append(
                {
                    "instruction": instruction,
                    "distance": distance[0] if distance else None,
                    "distance_meters": round(distance[1]) if distance else None,
                    "duration_seconds": self._parse_duration(instruction),
                }
            )
        return steps

    async def get_directions(
        self,
        origin_lat: float,
        origin_lng: float,
        destination_lat: float,
        destination_lng: float,
        mode: str = "driving",
        alternatives: bool = False,
        avoid: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Get directions by scraping the Google Maps directions pane.

        This previously returned a haversine great-circle distance divided by a
        hard-coded speed table, presented as a route. That number bore no
        relation to any road: it ignored roads entirely. It has been removed,
        and there is deliberately no fallback to it -- a scrape failure returns
        an error, because a plausible-looking wrong route is worse than no
        route.

        Args:
            origin_lat/origin_lng: Start coordinates.
            destination_lat/destination_lng: End coordinates.
            mode: One of driving, walking, bicycling, transit.
            alternatives: Return every route Google offers, not just the first.
            avoid: Any of tolls, highways, ferries (driving only).

        Returns:
            ``{"routes": [...], "mode": ..., "source": "google_maps_scrape"}``
            or an error dict with ``status_code`` 400 or 502.
        """
        await self._ensure_initialized()

        mode_key = (mode or "driving").lower()
        if mode_key not in self.TRAVEL_MODES:
            return {
                "error": True,
                "status_code": 400,
                "message": f"Unsupported travel mode {mode!r}. Expected one of {', '.join(self.TRAVEL_MODES)}.",
            }

        url = (
            "https://www.google.com/maps/dir/?api=1"
            f"&origin={origin_lat},{origin_lng}"
            f"&destination={destination_lat},{destination_lng}"
            f"&travelmode={mode_key}"
        )
        if avoid:
            allowed_avoid = [a for a in avoid if a in ("tolls", "highways", "ferries")]
            if allowed_avoid:
                url += "&avoid=" + "|".join(allowed_avoid)

        from app.services.google_maps_scraper import GoogleMapsScraper

        proxy = None
        if ENABLE_PROXY:
            proxy = proxy_for(GOOGLE_MAPS_HOST)

        scraper = GoogleMapsScraper(proxy=proxy, headless=True)
        try:
            page, context = await scraper._create_page("en")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(self.DIRECTIONS_SETTLE_SECONDS)

                routes = await self._extract_direction_routes(
                    page, max_routes=5 if alternatives else 1
                )
                if not routes:
                    # No route card was rendered. Could be a consent wall, a
                    # bot check, a layout change, or genuinely no route between
                    # these points -- we cannot tell them apart, so we say we
                    # could not read it rather than inventing a distance.
                    return {
                        "error": True,
                        "status_code": 502,
                        "message": (
                            "Could not read a route from Google Maps for these "
                            "coordinates. No estimate is returned in place of a "
                            "real route."
                        ),
                    }

                # Google renders the step list for the *selected* route only.
                # Attaching it to every alternative would be inventing turns for
                # routes we never looked at, so alternatives carry no steps and
                # say so.
                steps = await self._extract_direction_steps(page)
                for index, route in enumerate(routes):
                    if index == 0:
                        route["steps"] = steps or []
                        route["steps_available"] = steps is not None
                    else:
                        route["steps"] = []
                        route["steps_available"] = False

                return {
                    "routes": routes,
                    "mode": mode_key,
                    "source": "google_maps_scrape",
                    "scraped_at": datetime.now().isoformat(),
                }
            finally:
                await context.close()
        except Exception as e:
            logger.error(f"Directions scrape failed: {e}", exc_info=True)
            return {
                "error": True,
                "status_code": 502,
                "message": f"Directions scrape failed: {e}",
            }
        finally:
            await scraper.close()

    # =========================================================================
    # Menus
    # =========================================================================

    # Candidate selectors for a Google-rendered menu item. Tried in order.
    _MENU_ITEM_SELECTORS = (
        'div[jsaction*="dish"]',
        'div[aria-label="Menu"] div[role="listitem"]',
        'div[role="region"][aria-label*="Menu"] div[role="listitem"]',
        "div.PZrGGe",
    )

    _MENU_SECTION_SELECTORS = (
        'div[role="region"][aria-label*="Menu"]',
        'div[aria-label="Menu"]',
        'button[data-tab-index][aria-label*="Menu"]',
    )

    @staticmethod
    def _parse_menu_item(text: str, include_prices: bool, include_descriptions: bool) -> Optional[Dict[str, Any]]:
        """Turn one rendered menu item into a record, or None if it is not one."""
        import re

        lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
        if not lines:
            return None

        price = None
        price_line_index = None
        price_pattern = re.compile(r"^[^\w]*(?:[$£€¥₹]|USD|EUR|GBP)\s?\d[\d.,]*", re.IGNORECASE)
        for index, line in enumerate(lines):
            if price_pattern.search(line):
                price = line
                price_line_index = index
                break

        name = lines[0]
        if name == price and len(lines) > 1:
            name = lines[1]
        if not name:
            return None

        description = None
        if include_descriptions:
            body = [
                ln
                for index, ln in enumerate(lines[1:], start=1)
                if index != price_line_index and ln != name
            ]
            if body:
                description = " ".join(body)

        item: Dict[str, Any] = {"name": name}
        if include_prices:
            item["price"] = price
        if include_descriptions:
            item["description"] = description
        return item

    async def _extract_menu_items(
        self,
        page,
        include_prices: bool,
        include_descriptions: bool
    ) -> Optional[List[Dict[str, Any]]]:
        """Read Google's inline menu items.

        Returns None when no menu region was rendered at all -- distinct from
        ``[]``, which would mean a menu region that contained no items.
        """
        section_present = False
        for selector in self._MENU_SECTION_SELECTORS:
            if await page.query_selector(selector):
                section_present = True
                break

        nodes = []
        for selector in self._MENU_ITEM_SELECTORS:
            nodes = await page.query_selector_all(selector)
            if nodes:
                break

        if not nodes and not section_present:
            return None

        items: List[Dict[str, Any]] = []
        for node in nodes:
            text = (await node.inner_text()) or ""
            parsed = self._parse_menu_item(text, include_prices, include_descriptions)
            if parsed is not None:
                items.append(parsed)
        return items

    async def extract_menu(
        self,
        place_id: str,
        include_prices: bool = True,
        include_descriptions: bool = True,
        categorize: bool = True
    ) -> Dict[str, Any]:
        """
        Extract a place's menu by scraping the menu Google renders.

        This previously returned ``{"menu": [], "message": ...}`` for every
        place, which is indistinguishable from "this place has no menu". The
        four outcomes are now reported distinctly in ``menu_status``:

        - ``scraped`` -- Google rendered a menu and it was read.
        - ``no_menu`` -- the place has no menu link and Google rendered no menu
          section. A legitimately empty result.
        - ``external_menu_not_scraped`` -- the place's menu lives on a
          third-party site (the ``menu_link``). We do not scrape arbitrary
          third-party sites, so the items are *not* available; the link is
          returned so the caller can follow it. This is not an empty menu.
        - ``unreadable`` -- a menu section was present but no items could be
          read from it, i.e. we failed, not the place.

        Args:
            place_id: Place whose menu is wanted.
            include_prices: Include a ``price`` field per item.
            include_descriptions: Include a ``description`` field per item.
            categorize: Group items under ``categories`` as well as listing them.

        Returns:
            The menu result, or an error dict on lookup/scrape failure.
        """
        await self._ensure_initialized()

        place_result = await self.get_place_by_id(place_id)
        if place_result.get("error"):
            return place_result

        place = place_result.get("place") or {}
        menu_link = place.get("menu_link")
        place_url = place.get("google_maps_url") or f"https://www.google.com/maps/place/?q=place_id:{place_id}"

        from app.services.google_maps_scraper import GoogleMapsScraper

        proxy = None
        if ENABLE_PROXY:
            proxy = proxy_for(GOOGLE_MAPS_HOST)

        scraper = GoogleMapsScraper(proxy=proxy, headless=True)
        try:
            page, context = await scraper._create_page("en")
            try:
                await page.goto(place_url, wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(self.PAGE_SETTLE_SECONDS)
                items = await self._extract_menu_items(page, include_prices, include_descriptions)
            finally:
                await context.close()
        except Exception as e:
            logger.error(
                    "Menu scrape failed for %s: %s", scrub(place_id), scrub(e),
                    exc_info=True,
                )
            return {
                "error": True,
                "status_code": 502,
                "message": f"Could not load the place page to read its menu: {e}",
            }
        finally:
            await scraper.close()

        if items:
            categories: Dict[str, List[Dict[str, Any]]] = {}
            if categorize:
                # Google's inline menu does not label sections in a form we can
                # read reliably, so items land in a single "Menu" group rather
                # than being sorted into invented category names.
                categories = {"Menu": items}
            return {
                "menu_available": True,
                "menu_status": "scraped",
                "menu_link": menu_link,
                "menu": items,
                "categories": categories if categorize else {},
                "source": "google_maps_scrape",
            }

        if items == []:
            return {
                "menu_available": True,
                "menu_status": "unreadable",
                "menu_link": menu_link,
                "menu": [],
                "categories": {},
                "message": (
                    "A menu section was present on the place page but no items could be "
                    "read from it. This is a scrape failure, not an empty menu."
                ),
            }

        if menu_link:
            return {
                "menu_available": True,
                "menu_status": "external_menu_not_scraped",
                "menu_link": menu_link,
                "menu": [],
                "categories": {},
                "message": (
                    "This place's menu is hosted on a third-party site, which is not "
                    "scraped. Follow menu_link for the items. The empty menu list does "
                    "not mean the place has no menu."
                ),
            }

        return {
            "menu_available": False,
            "menu_status": "no_menu",
            "menu_link": None,
            "menu": [],
            "categories": {},
            "message": (
                "No menu link and no menu section were found on this place's Google "
                "Maps page. That is the basis for the empty result -- a dedicated "
                "menu tab was not opened."
            ),
        }

    async def batch_geocode(
        self,
        addresses: List[str]
    ) -> Dict[str, Any]:
        """
        Geocode multiple addresses.

        Uses Google Maps search to find coordinates for addresses.
        """
        try:
            await self._ensure_initialized()

            results = []
            successful = 0
            failed = 0

            for address in addresses:
                try:
                    # Search for the address
                    result = await self.search_and_wait(
                        query=address,
                        max_results=1,
                        timeout=30
                    )

                    if result.get("error") or not result.get("results"):
                        failed += 1
                        results.append({
                            "address": address,
                            "success": False,
                            "error": "Address not found"
                        })
                    else:
                        successful += 1
                        place = result["results"][0]
                        results.append({
                            "address": address,
                            "success": True,
                            "latitude": place.get("latitude"),
                            "longitude": place.get("longitude"),
                            "formatted_address": place.get("address"),
                            "place_id": place.get("cid")
                        })

                except Exception as e:
                    failed += 1
                    results.append({
                        "address": address,
                        "success": False,
                        "error": str(e)
                    })

            return {
                "results": results,
                "successful": successful,
                "failed": failed
            }

        except Exception as e:
            logger.error(f"Geocode error: {e}")
            return {"error": True, "message": str(e)}

    async def get_place_attributes(
        self,
        place_id: str
    ) -> Dict[str, Any]:
        """
        Get detailed attributes for a place.

        Returns attributes from the main place data.
        """
        try:
            await self._ensure_initialized()

            # Get place data
            place_result = await self.get_place_by_id(place_id)

            if place_result.get("error"):
                return place_result

            place = place_result.get("place", {})

            attributes = {
                "service_options": place.get("service_options") or [],
                "accessibility": place.get("accessibility") or [],
                "amenities": place.get("amenities") or [],
                "highlights": place.get("description"),
                "price_level": place.get("price_level"),
                "price_per_person": place.get("price_per_person")
            }

            return {"attributes": attributes}

        except Exception as e:
            logger.error(f"Get attributes error: {e}")
            return {"error": True, "message": str(e)}

    async def get_place_history(
        self,
        place_id: str,
        field: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        api_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get recorded change history for a place.

        History is real: it is the snapshot trail the monitor scheduler writes
        each time it re-scrapes a place and sees a tracked field change. It is
        owner-scoped -- only this caller's monitors are consulted.

        Where no monitor has ever covered the place, the response says so
        (``monitored: false``) instead of returning an empty list that would
        read as "we looked and nothing changed".

        Args:
            place_id: Place whose history is wanted.
            field: Only entries in which this field changed.
            start_date: ISO-8601 lower bound (inclusive).
            end_date: ISO-8601 upper bound (inclusive; a bare date covers the
                whole of that day).
            api_key: Caller's API key; scopes the lookup to that owner.
        """
        from app.services import google_maps_monitors as monitors

        try:
            return await monitors.get_place_history(
                owner=self._owner(api_key),
                place_id=place_id,
                field=field,
                start_date=start_date,
                end_date=end_date,
            )
        except ValueError as e:
            return {"error": True, "status_code": 400, "message": str(e)}

    # =========================================================================
    # Reservation availability
    # =========================================================================

    _RESERVE_MODULE_SELECTORS = (
        '[data-item-id="reserve"]',
        'div[aria-label*="Reserve a table"]',
        'a[href*="reserve.google.com"]',
    )

    _RESERVE_SLOT_SELECTORS = (
        'div[jsaction*="reserve"] button[aria-label*=":"]',
        'div[aria-label*="Reserve a table"] button',
        'button[jsaction*="slot"]',
    )

    @staticmethod
    def _parse_slot_label(text: str) -> Optional[str]:
        """Pull a clock time out of a slot button's label, or None."""
        import re

        match = re.search(r"\b\d{1,2}:\d{2}\s*(?:AM|PM)?\b", text or "", re.IGNORECASE)
        return match.group(0).strip() if match else None

    async def _extract_reservation_slots(self, page) -> Optional[List[str]]:
        """Read rendered reservation slots.

        Returns None when no reservation module was rendered at all, which is
        different from ``[]`` (a module that offers no slots).
        """
        module_present = False
        for selector in self._RESERVE_MODULE_SELECTORS:
            if await page.query_selector(selector):
                module_present = True
                break

        nodes = []
        for selector in self._RESERVE_SLOT_SELECTORS:
            nodes = await page.query_selector_all(selector)
            if nodes:
                break

        if not nodes and not module_present:
            return None

        slots: List[str] = []
        for node in nodes:
            label = self._parse_slot_label((await node.inner_text()) or "")
            if label and label not in slots:
                slots.append(label)
        return slots

    async def check_availability(
        self,
        place_id: str,
        date: str,
        party_size: int
    ) -> Dict[str, Any]:
        """
        Check reservation availability by scraping the place page.

        This previously returned ``time_slots: []`` with an explanatory message
        for every place, which reads to a client as "fully booked". The
        outcomes are now distinct in ``availability_status``:

        - ``slots_found`` -- Google rendered bookable slots and they were read.
        - ``no_slots`` -- a reservation module was rendered but offered no
          slots. A real "nothing available" answer.
        - ``external_provider`` -- the place books through a partner whose
          widget Google does not render inline. Slots are not readable and the
          empty list must not be read as "fully booked"; ``booking_url`` is
          returned instead.
        - ``no_reservation_integration`` -- this place takes no online
          reservations at all.

        Caveat, stated rather than hidden: ``date`` and ``party_size`` cannot
        be applied to Google's inline module, which renders the provider's
        default view. ``filters_applied`` is false whenever slots come back, so
        a caller is never told the slots match a party size we could not
        request.

        Args:
            place_id: Place to check.
            date: Requested date (ISO-8601), echoed back; see the caveat.
            party_size: Requested party size, echoed back; see the caveat.
        """
        await self._ensure_initialized()

        place_result = await self.get_place_by_id(place_id)
        if place_result.get("error"):
            return place_result

        place = place_result.get("place") or {}
        reserve_link = place.get("reserve_link")
        place_url = place.get("google_maps_url") or f"https://www.google.com/maps/place/?q=place_id:{place_id}"

        from app.services.google_maps_scraper import GoogleMapsScraper

        proxy = None
        if ENABLE_PROXY:
            proxy = proxy_for(GOOGLE_MAPS_HOST)

        scraper = GoogleMapsScraper(proxy=proxy, headless=True)
        try:
            page, context = await scraper._create_page("en")
            try:
                await page.goto(place_url, wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(self.PAGE_SETTLE_SECONDS)
                slots = await self._extract_reservation_slots(page)
            finally:
                await context.close()
        except Exception as e:
            logger.error(
                    "Availability scrape failed for %s: %s", scrub(place_id), scrub(e),
                    exc_info=True,
                )
            return {
                "error": True,
                "status_code": 502,
                "message": f"Could not load the place page to check availability: {e}",
            }
        finally:
            await scraper.close()

        base = {
            "place_id": place_id,
            "requested_date": date,
            "requested_party_size": party_size,
            "booking_url": reserve_link,
        }

        if slots:
            return {
                **base,
                "reservations_available": True,
                "availability_status": "slots_found",
                "time_slots": slots,
                "filters_applied": False,
                "message": (
                    "Slots read from the reservation module on the place page. The "
                    "requested date and party size were not applied to it, so these "
                    "are the provider's default slots."
                ),
            }

        if slots == []:
            return {
                **base,
                "reservations_available": True,
                "availability_status": "no_slots",
                "time_slots": [],
                "filters_applied": False,
                "message": (
                    "A reservation module was found on the page and no bookable slots "
                    "were read from it. The requested date and party size were not "
                    "applied, so this reflects the provider's default view."
                ),
            }

        if reserve_link:
            return {
                **base,
                "reservations_available": True,
                "availability_status": "external_provider",
                "time_slots": [],
                "filters_applied": False,
                "message": (
                    "This place books through a partner whose slots are not rendered on "
                    "the Google Maps page, so no slots could be read. The empty list "
                    "does not mean the place is fully booked -- follow booking_url."
                ),
            }

        return {
            **base,
            "reservations_available": False,
            "availability_status": "no_reservation_integration",
            "time_slots": [],
            "filters_applied": False,
            "message": (
                "No reservation link and no reservation module were found on this "
                "place's Google Maps page, so no online booking through Google was "
                "detected."
            ),
        }


# Singleton instance
google_maps_service = GoogleMapsService()
