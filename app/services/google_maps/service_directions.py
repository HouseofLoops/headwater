"""
Directions scraping for GoogleMapsService.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, ClassVar

from app.core.proxy import ENABLE_PROXY, proxy_for
from app.services.google_maps.constants import GOOGLE_MAPS_HOST

# Logs under the facade module's name so log routing and filters keyed on
# ``app.services.google_maps_service`` are unaffected by the split.
logger = logging.getLogger("app.services.google_maps_service")


class DirectionsMixin:
    """Directions methods of GoogleMapsService."""

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
    _DISTANCE_UNITS: ClassVar[dict[str, float]] = {
        "km": 1000.0,
        "m": 1.0,
        "mi": 1609.344,
        "ft": 0.3048,
    }

    @staticmethod
    def _format_duration(seconds: int | None) -> str | None:
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
    def _parse_duration(text: str) -> int | None:
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
    def _parse_distance(cls, text: str) -> tuple[str, float] | None:
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
    def _parse_route_card(cls, text: str) -> dict[str, Any] | None:
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

    async def _extract_direction_routes(self, page, max_routes: int) -> list[dict[str, Any]]:
        """Read the route cards Google rendered. Empty list means none were found."""
        nodes = []
        for selector in self._ROUTE_SELECTORS:
            nodes = await page.query_selector_all(selector)
            if nodes:
                break

        routes: list[dict[str, Any]] = []
        for node in nodes[:max_routes]:
            text = await node.inner_text()
            parsed = self._parse_route_card(text or "")
            if parsed is not None:
                routes.append(parsed)
        return routes

    async def _extract_direction_steps(self, page) -> list[dict[str, Any]] | None:
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

        steps: list[dict[str, Any]] = []
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
        avoid: list[str] | None = None,
    ) -> dict[str, Any]:
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

                routes = await self._extract_direction_routes(page, max_routes=5 if alternatives else 1)
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
