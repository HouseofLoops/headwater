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

import asyncio
import logging
import uuid
from typing import Any

from app.core.log_safety import scrub
from app.core.proxy import ENABLE_PROXY, proxy_for

# Most methods live in mixins under app.services.google_maps; the
# GoogleMapsService class and the google_maps_service singleton stay here so
# every existing import and patch target keeps resolving to this module.
from app.services.google_maps.constants import GOOGLE_MAPS_HOST
from app.services.google_maps.service_area_search import AreaSearchMixin
from app.services.google_maps.service_directions import DirectionsMixin
from app.services.google_maps.service_menu import MenuMixin
from app.services.google_maps.service_monitors import MonitorsMixin
from app.services.google_maps.service_place_content import PlaceContentMixin
from app.services.google_maps.service_reservations import ReservationsMixin

logger = logging.getLogger(__name__)


class GoogleMapsService(
    AreaSearchMixin,
    PlaceContentMixin,
    MonitorsMixin,
    DirectionsMixin,
    MenuMixin,
    ReservationsMixin,
):
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

    async def health_check(self) -> dict[str, Any]:
        """
        Check if the scraping service is healthy.

        Returns:
            Health status dictionary
        """
        try:
            await self._ensure_initialized()

            # For native scraping, we just verify Playwright can be imported
            try:
                from playwright.async_api import async_playwright  # noqa: F401 - availability probe

                return {
                    "healthy": True,
                    "status_code": 200,
                    "service": "google-maps-native-scraper",
                    "mode": "native-playwright",
                }
            except ImportError:
                return {"healthy": False, "error": "Playwright not installed", "service": "google-maps-native-scraper"}

        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return {"healthy": False, "error": str(e), "service": "google-maps-native-scraper"}

    async def create_search_job(
        self,
        query: str,
        owner: str = "anonymous",
        language: str = "en",
        max_results: int = 20,
        depth: int = 1,
        email_extraction: bool = False,
        zoom: int = 15,
        geo_coordinates: str | None = None,
    ) -> dict[str, Any]:
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
                    logger.info("Using proxy for Google Maps scraping")

            # Start background task
            asyncio.create_task(self._scraper_module.run_scrape_job(job, proxy=proxy))

            logger.info("Created job %s for query: %s", scrub(job_id), scrub(query))

            return {"job_id": job_id, "id": job_id, "status": "pending", "message": "Job created and started"}

        except Exception as e:
            logger.error(f"Error creating search job: {e}")
            return {"error": True, "message": str(e)}

    async def get_job_status(self, job_id: str, owner: str) -> dict[str, Any]:
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
                return {"error": True, "status_code": 404, "message": "Job not found"}

            # Map internal status to expected format
            status_map = {"pending": "pending", "running": "working", "completed": "completed", "failed": "failed"}

            return {
                "job_id": job.id,
                "status": status_map.get(job.status.value, job.status.value),
                "progress": job.progress,
                "total": job.total,
                "error": job.error,
                "created_at": job.created_at.isoformat(),
                "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            }

        except Exception as e:
            logger.error(f"Error getting job status: {e}")
            return {"error": True, "message": str(e)}

    async def get_job_results(self, job_id: str, owner: str, format: str = "json") -> dict[str, Any]:
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
                return {"error": True, "status_code": 404, "message": "Job not found"}

            if job.status != self._scraper_module.JobStatus.COMPLETED:
                return {"error": True, "message": f"Job not completed. Current status: {job.status.value}"}

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

            return {"results": job.results, "format": "json", "count": len(job.results), "job_id": job_id}

        except Exception as e:
            logger.error(f"Error getting job results: {e}")
            return {"error": True, "message": str(e)}

    async def list_jobs(
        self, owner: str, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
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
            jobs = await store.list_for_owner(owner, status=status, limit=limit, offset=offset)

            # Return in gosom-compatible format
            return [job.to_dict() for job in jobs]

        except Exception as e:
            logger.error(f"Error listing jobs: {e}")
            return {"error": True, "message": str(e)}

    async def delete_job(self, job_id: str, owner: str) -> dict[str, Any]:
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
                return {"error": True, "status_code": 404, "message": "Job not found"}

        except Exception as e:
            logger.error(f"Error deleting job: {e}")
            return {"error": True, "message": str(e)}

    async def search_and_wait(
        self,
        query: str,
        owner: str = "anonymous",
        language: str = "en",
        max_results: int = 20,
        depth: int = 1,
        email_extraction: bool = False,
        zoom: int = 15,
        geo_coordinates: str | None = None,
        timeout: int = 300,
        poll_interval: int = 2,
    ) -> dict[str, Any]:
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
            geo_coordinates=geo_coordinates,
        )

        if job_response.get("error"):
            return job_response

        job_id = job_response.get("job_id") or job_response.get("id")
        if not job_id:
            return {"error": True, "message": "No job_id in response", "response": job_response}

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
                return {"error": True, "status": "failed", "job_id": job_id, "details": status_response}

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        return {
            "error": True,
            "status": "timeout",
            "job_id": job_id,
            "message": f"Job did not complete within {timeout} seconds",
        }

    def process_place_data(self, raw_data: list[dict]) -> list[dict[str, Any]]:
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
                "hours": place.get("open_hours")
                or place.get("hours")
                or place.get("opening_hours")
                or place.get("working_hours"),
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
                    "youtube": place.get("youtube"),
                },
            }

            # Clean up None values in social_media
            processed_place["social_media"] = {k: v for k, v in processed_place["social_media"].items() if v} or None

            # Clean up empty lists/dicts
            for key in [
                "service_options",
                "accessibility",
                "amenities",
                "review_topics",
                "sample_reviews",
                "related_places",
            ]:
                if not processed_place.get(key):
                    processed_place[key] = None
            if not processed_place.get("popular_times"):
                processed_place["popular_times"] = None

            processed.append(processed_place)

        return processed

    # =========================================================================
    # Extended Feature Methods
    # =========================================================================

    async def get_place_by_id(self, place_id: str) -> dict[str, Any]:
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

    async def lookup_place(self, url: str | None = None, place_id: str | None = None) -> dict[str, Any]:
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
            from app.core.proxy import ENABLE_PROXY, proxy_for
            from app.services.google_maps_scraper import GoogleMapsScraper

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


# Singleton instance
google_maps_service = GoogleMapsService()
