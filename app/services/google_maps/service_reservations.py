"""
Reservation availability for GoogleMapsService.
"""
import logging
import asyncio
from typing import Optional, List, Dict, Any

from app.core.proxy import ENABLE_PROXY, proxy_for
from app.core.log_safety import scrub
from app.services.google_maps.constants import GOOGLE_MAPS_HOST

# Logs under the facade module's name so log routing and filters keyed on
# ``app.services.google_maps_service`` are unaffected by the split.
logger = logging.getLogger("app.services.google_maps_service")


class ReservationsMixin:
    """Reservation availability methods of GoogleMapsService."""
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
