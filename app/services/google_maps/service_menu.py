"""
Menu extraction for GoogleMapsService.
"""
import asyncio
import logging
from typing import Any

from app.core.log_safety import scrub
from app.core.proxy import ENABLE_PROXY, proxy_for
from app.services.google_maps.constants import GOOGLE_MAPS_HOST

# Logs under the facade module's name so log routing and filters keyed on
# ``app.services.google_maps_service`` are unaffected by the split.
logger = logging.getLogger("app.services.google_maps_service")


class MenuMixin:
    """Menu extraction methods of GoogleMapsService."""
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
    def _parse_menu_item(text: str, include_prices: bool, include_descriptions: bool) -> dict[str, Any] | None:
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

        item: dict[str, Any] = {"name": name}
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
    ) -> list[dict[str, Any]] | None:
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

        items: list[dict[str, Any]] = []
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
    ) -> dict[str, Any]:
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
            categories: dict[str, list[dict[str, Any]]] = {}
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
