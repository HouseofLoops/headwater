"""
Area and multi-query searches for GoogleMapsService.

Nearby, grid, bounding-box and location searches, bulk search and
competitor analysis -- everything that fans out over ``search_and_wait``.
"""
import logging
import math
from typing import Optional, List, Dict, Any, Tuple

from app.core.log_safety import scrub

# Logs under the facade module's name so log routing and filters keyed on
# ``app.services.google_maps_service`` are unaffected by the split.
logger = logging.getLogger("app.services.google_maps_service")


class AreaSearchMixin:
    """Area, grid and multi-query search methods of GoogleMapsService."""
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
