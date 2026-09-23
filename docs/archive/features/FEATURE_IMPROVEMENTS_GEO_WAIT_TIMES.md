# Feature Improvements: Geo-Targeting & Live Wait Times

**Date:** December 28, 2025
**Status:** IMPLEMENTED

This document outlines the upgrades made to Headwater's geo-targeting to match DataForSEO's "Excellent" rating and the implementation of live wait times extraction.

---

## Current State

### Geo-Targeting (Current: "Good")

| Feature | Current Implementation | Gap |
|---------|------------------------|-----|
| Coordinate search | `lat,lng` format | Basic |
| Zoom level | 1-21 (15 default) | OK |
| Radius search | 100m - 50km | OK |
| Grid-based search | Not implemented | Missing |
| Location name lookup | Not implemented | Missing |
| Bounding box search | Not implemented | Missing |
| ZIP/postal code | Not implemented | Missing |

**Current URL format:**
```
https://www.google.com/maps/search/restaurants/@40.7128,-74.0060,15z
```

### Live Wait Times (Current: "Planned")

| Feature | Current Implementation | Gap |
|---------|------------------------|-----|
| Popular times (historical) | Extracts hourly busy % | OK |
| Live wait time | Not implemented | Missing |
| Current busyness | Not implemented | Missing |

---

## Improvement Plan: Enhanced Geo-Targeting

### 1. Grid-Based Search (Like DataForSEO)

DataForSEO's key feature is grid-based rank tracking. A 7x7 grid creates 49 coordinate points across an area:

```
+---+---+---+---+---+---+---+
| 1 | 2 | 3 | 4 | 5 | 6 | 7 |
+---+---+---+---+---+---+---+
| 8 | 9 |10 |11 |12 |13 |14 |
+---+---+---+---+---+---+---+
...
+---+---+---+---+---+---+---+
|43 |44 |45 |46 |47 |48 |49 |
+---+---+---+---+---+---+---+
```

**Implementation:**

Add to `app/services/google_maps_service.py`:

```python
from typing import List, Tuple
import math


def calculate_grid_coordinates(
    center_lat: float, center_lng: float, radius_km: float, grid_size: int = 7
) -> List[Tuple[float, float]]:
    """
    Generate a grid of coordinates around a center point.

    Args:
        center_lat: Center latitude
        center_lng: Center longitude
        radius_km: Radius in kilometers (distance from center to edge)
        grid_size: Number of points per side (e.g., 7 for 7x7 = 49 points)

    Returns:
        List of (lat, lng) tuples
    """
    # Earth's radius in km
    EARTH_RADIUS = 6371

    # Calculate the step size between grid points
    step_km = (radius_km * 2) / (grid_size - 1)

    # Convert km to degrees (approximate)
    lat_step = step_km / 111.32  # 1 degree lat = 111.32 km
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
) -> dict:
    """
    Search across a grid of coordinates for comprehensive coverage.

    Args:
        query: Search query
        center_lat: Center latitude
        center_lng: Center longitude
        radius_km: Search radius in km
        grid_size: Grid dimension (5 = 5x5 = 25 points)
        max_results_per_point: Max results per grid point

    Returns:
        Aggregated results with grid metadata
    """
    grid_coords = calculate_grid_coordinates(center_lat, center_lng, radius_km, grid_size)

    all_results = {}
    grid_data = []

    for idx, (lat, lng) in enumerate(grid_coords):
        results = await self.scraper.search(
            query=query,
            geo_coordinates=f"{lat},{lng}",
            max_results=max_results_per_point,
            zoom=16,  # Higher zoom for local focus
        )

        grid_data.append({"grid_index": idx, "lat": lat, "lng": lng, "results_count": len(results)})

        # Dedupe by place_id
        for place in results:
            place_id = place.get("place_id")
            if place_id and place_id not in all_results:
                place["grid_positions"] = [idx]
                all_results[place_id] = place
            elif place_id:
                all_results[place_id]["grid_positions"].append(idx)

    return {
        "query": query,
        "center": {"lat": center_lat, "lng": center_lng},
        "radius_km": radius_km,
        "grid_size": grid_size,
        "total_grid_points": len(grid_coords),
        "unique_places": len(all_results),
        "grid_metadata": grid_data,
        "places": list(all_results.values()),
    }
```

**New API Endpoint:**

Add to `app/api/google_maps/google_maps_api.py`:

```python
class GridSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    center_lat: float = Field(..., ge=-90, le=90)
    center_lng: float = Field(..., ge=-180, le=180)
    radius_km: float = Field(default=5.0, ge=0.1, le=50)
    grid_size: int = Field(default=5, ge=3, le=11)  # 3x3 to 11x11
    max_results_per_point: int = Field(default=10, ge=1, le=20)


@router.post("/grid-search", response_model=dict)
async def grid_search(request: GridSearchRequest, api_key: str = Depends(verify_api_key)):
    """
    Perform grid-based search across multiple coordinates.

    Like DataForSEO's calculate_rectangles, this searches a grid
    of points to find all businesses in an area, not just those
    visible from a single viewpoint.
    """
    result = await google_maps_service.grid_search(
        query=request.query,
        center_lat=request.center_lat,
        center_lng=request.center_lng,
        radius_km=request.radius_km,
        grid_size=request.grid_size,
        max_results_per_point=request.max_results_per_point,
    )
    return result
```

### 2. Bounding Box Search

Allow searching within a rectangular area defined by two corners:

```python
class BoundingBoxRequest(BaseModel):
    query: str
    north_lat: float = Field(..., ge=-90, le=90)  # Top
    south_lat: float = Field(..., ge=-90, le=90)  # Bottom
    east_lng: float = Field(..., ge=-180, le=180)  # Right
    west_lng: float = Field(..., ge=-180, le=180)  # Left


async def bounding_box_search(
    self, query: str, north_lat: float, south_lat: float, east_lng: float, west_lng: float, grid_density: int = 5
) -> dict:
    """
    Search within a bounding box by creating a grid.
    """
    # Calculate center and radius
    center_lat = (north_lat + south_lat) / 2
    center_lng = (east_lng + west_lng) / 2

    # Calculate radius from center to corner
    lat_diff = abs(north_lat - south_lat) / 2
    lng_diff = abs(east_lng - west_lng) / 2

    # Convert to km (approximate)
    radius_km = max(lat_diff * 111.32, lng_diff * 111.32 * math.cos(math.radians(center_lat)))

    return await self.grid_search(
        query=query, center_lat=center_lat, center_lng=center_lng, radius_km=radius_km, grid_size=grid_density
    )
```

### 3. Location Name to Coordinates

Add geocoding lookup for city/ZIP names:

```python
# Location database (can be loaded from file or API)
LOCATION_DATABASE = {
    "gladstone, or": {"lat": 45.3807, "lng": -122.5940, "type": "city"},
    "portland, or": {"lat": 45.5152, "lng": -122.6784, "type": "city"},
    "97027": {"lat": 45.3807, "lng": -122.5940, "type": "zip"},
    # ... more locations
}


async def search_by_location_name(
    self,
    query: str,
    location: str,  # "Portland, OR" or "97027"
    radius_km: float = 5.0,
) -> dict:
    """
    Search using a location name instead of coordinates.
    """
    location_lower = location.lower().strip()

    # Check database first
    if location_lower in LOCATION_DATABASE:
        loc = LOCATION_DATABASE[location_lower]
        return await self.grid_search(query=query, center_lat=loc["lat"], center_lng=loc["lng"], radius_km=radius_km)

    # Fall back to geocoding
    geocode_result = await self.geocode(location)
    if geocode_result:
        return await self.grid_search(
            query=query,
            center_lat=float(geocode_result["latitude"]),
            center_lng=float(geocode_result["longitude"]),
            radius_km=radius_km,
        )

    raise ValueError(f"Could not resolve location: {location}")
```

---

## Improvement Plan: Live Wait Times

### Understanding Google Maps Wait Times

Google Maps displays two types of wait information:

1. **Popular Times (Historical)** - Already extracted
   - "Usually not too busy"
   - "Usually a little busy"
   - Hourly percentages: "85% busy at 12 PM"

2. **Live Wait Time** - Not yet extracted
   - "Usually X min wait"
   - "Live: Busier than usual"
   - "Live: 15 min wait"

### Implementation

Add to `app/services/google_maps_scraper.py` in the `_extract_place_details` method:

```python
# Extract live wait time and current busyness
try:
    # Look for live busyness indicator
    live_busy = page.locator(
        "div:has-text('Live:'), "
        "span:has-text('Busier than usual'), "
        "span:has-text('Less busy than usual'), "
        "span:has-text('As busy as it gets')"
    )

    if await live_busy.count() > 0:
        live_text = await live_busy.first.text_content()
        if live_text:
            place["live_busyness"] = live_text.strip()

    # Look for wait time specifically
    wait_selectors = ["span:has-text('min wait')", "div:has-text('wait time')", "[aria-label*='wait']"]

    for selector in wait_selectors:
        wait_elem = page.locator(selector)
        if await wait_elem.count() > 0:
            wait_text = await wait_elem.first.text_content()
            if wait_text:
                # Parse "Usually 15 min wait" or "Live: 20 min wait"
                match = re.search(r"(\d+)\s*min\s*wait", wait_text, re.IGNORECASE)
                if match:
                    place["wait_time_minutes"] = int(match.group(1))
                    place["wait_time_raw"] = wait_text.strip()
                break

    # Extract "Usually not too busy" type messages
    usual_busy = page.locator(
        "span:has-text('Usually not too busy'), "
        "span:has-text('Usually a little busy'), "
        "span:has-text('Usually not busy')"
    )

    if await usual_busy.count() > 0:
        usual_text = await usual_busy.first.text_content()
        if usual_text:
            place["typical_busyness"] = usual_text.strip()

except Exception as e:
    logger.debug(f"Could not extract wait time: {e}")
```

### Update PlaceResult Model

Add to `app/api/google_maps/google_maps_api.py`:

```python
class PlaceResult(BaseModel):
    # ... existing fields ...

    # New wait time fields
    wait_time_minutes: Optional[int] = Field(default=None, description="Current/typical wait time in minutes")
    wait_time_raw: Optional[str] = Field(default=None, description="Raw wait time text from Google")
    live_busyness: Optional[str] = Field(
        default=None, description="Live busyness indicator (e.g., 'Busier than usual')"
    )
    typical_busyness: Optional[str] = Field(default=None, description="Typical busyness (e.g., 'Usually not too busy')")
```

### Live Wait Time Selectors to Try

Based on Google Maps HTML structure:

```python
WAIT_TIME_SELECTORS = [
    # Direct wait time mentions
    "span.fontBodyMedium:has-text('min wait')",
    "div[jsaction*='wait']",
    # Live indicator with wait
    "div:has(> span:has-text('Live')) + span:has-text('wait')",
    # Restaurant-specific wait
    "[data-tooltip*='wait time']",
    "button[aria-label*='wait time']",
    # Aria labels
    "[aria-label*='minute wait']",
    "[aria-label*='wait time']",
    # Busyness indicators
    "span:has-text('Busier than usual')",
    "span:has-text('Less busy than usual')",
    "span:has-text('Not too busy')",
    "span:has-text('A little busy')",
]
```

---

## New API Response Example

After implementing both features:

```json
{
  "place_id": "ChIJ...",
  "name": "Popular Restaurant",
  "address": "123 Main St, Portland, OR",
  "rating": 4.5,
  "review_count": "523",

  "popular_times": {
    "Saturday": [
      {"hour": "12 PM", "busy_percent": 85},
      {"hour": "1 PM", "busy_percent": 92}
    ]
  },

  "wait_time_minutes": 15,
  "wait_time_raw": "Usually 15 min wait",
  "live_busyness": "Live: Busier than usual",
  "typical_busyness": "Usually a little busy",

  "grid_positions": [12, 13, 17, 18]
}
```

---

## Implementation Priority

### Phase 1: Grid Search (High Impact)
1. Implement `calculate_grid_coordinates()` function
2. Add `grid_search()` method to service
3. Create `/grid-search` API endpoint
4. Update comparison doc: Geo-targeting "Good" -> "Excellent"

### Phase 2: Live Wait Times (Medium Impact)
1. Add wait time selectors to scraper
2. Update PlaceResult model with new fields
3. Test with restaurant locations
4. Update comparison doc: Live Wait Times "Planned" -> "Yes"

### Phase 3: Bounding Box & Location Names
1. Implement bounding box search
2. Add location name resolver
3. Create location database (or integrate with geocoding)

---

## Testing

### Grid Search Test
```bash
curl -X POST "http://localhost:8000/api/v1/google-maps/grid-search" \
  -H "X-API-Key: testapikey" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "restaurants",
    "center_lat": 45.3807,
    "center_lng": -122.5940,
    "radius_km": 2,
    "grid_size": 3
  }'
```

### Wait Time Test
```bash
# Test with a restaurant known to have wait times
curl "http://localhost:8000/api/v1/google-maps/place/ChIJ...?include_wait_times=true" \
  -H "X-API-Key: testapikey"
```

---

## Implementation Status

All features have been implemented:

| Feature | Before | After | Status |
|---------|--------|-------|--------|
| Geo-targeting | Good | **Excellent** | DONE |
| Live Wait Times | Planned | **Yes** | DONE |
| Grid-based search | No | **Yes** | DONE |
| Bounding box | No | **Yes** | DONE |
| Location names | No | **Yes** | DONE |

Headwater now matches or exceeds DataForSEO's geo-targeting capabilities while adding live wait times that DataForSEO doesn't offer.

---

## Sources

- [DataForSEO Google Maps API](https://dataforseo.com/apis/serp-api/google-maps-api)
- [DataForSEO Grid Rank Tracker Guide](https://dataforseo.com/help-center/build-grid-rank-tracker-maps)
- [DataForSEO Locations API](https://docs.dataforseo.com/v3/keywords_data-google-locations/)
