"""
Place-panel extraction for GoogleMapsScraper.

``_extract_place_details`` and its helpers, mixed into GoogleMapsScraper.
"""
import asyncio
import logging
import re
from typing import Any

from app.services.google_maps.scraper_errors import (
    CORE_PLACE_FIELDS,
    REQUIRED_PLACE_FIELDS,
    PlaceExtractionError,
    ScraperError,
)

# Logs under the facade module's name so log routing and filters keyed on
# ``app.services.google_maps_scraper`` are unaffected by the split.
logger = logging.getLogger("app.services.google_maps_scraper")
class PlaceDetailsMixin:
    """Place-details extraction methods of GoogleMapsScraper."""

    async def _extract_place_details(self, page) -> dict[str, Any] | None:
        """Extract detailed information from a place page.

        Returns:
            The place dict. It always carries ``partial``, ``selectors_stale``
            and ``missing_fields`` keys so a caller can tell a thin-but-real
            record from a parse failure.

        Raises:
            PlaceExtractionError: No required field (see
                :data:`REQUIRED_PLACE_FIELDS`) could be extracted. Previously
                this returned ``None`` and the caller dropped it silently,
                which is how a total parser failure became an empty success.
        """
        misses: list[str] = []
        try:
            # Wait longer for content to fully load
            await asyncio.sleep(2)

            # Wait for the main details panel
            with self._optional(misses, "details_panel"):
                await page.wait_for_selector("div[role='main']", timeout=5000)

            # Scroll down the details panel to load dynamic content
            # (popular times, reviews, related places are loaded on scroll)
            with self._optional(misses, "lazy_load_scroll"):
                main_panel = page.locator("div[role='main']").first
                if await main_panel.count() > 0:
                    # Scroll down in steps to trigger lazy loading
                    for scroll_step in range(6):
                        await main_panel.evaluate("el => el.scrollBy(0, 800)")
                        await asyncio.sleep(0.7)
                    await asyncio.sleep(1)
                    # Scroll back to top
                    await main_panel.evaluate("el => el.scrollTop = 0")
                    await asyncio.sleep(0.5)

            # Initialize place data with all available fields
            place = {
                "title": None,
                "cid": None,
                "link": None,
                "address": None,
                "phone": None,
                "website": None,
                "latitude": None,
                "longitude": None,
                "plus_code": None,
                "category": None,
                "review_rating": None,
                "review_count": None,
                "price_range": None,
                "price_per_person": None,
                "open_hours": None,
                "is_open_now": None,
                "description": None,
                "photos": [],
                "menu_link": None,
                "order_link": None,
                "reserve_link": None,
                "amenities": [],
                "service_options": [],
                "accessibility": [],
                "popular_times": {},
                "review_summary": None,
                "review_topics": [],
                "sample_reviews": [],
                "related_places": [],
            }

            # Get current URL for link and coordinates
            current_url = page.url
            place["link"] = current_url

            # Extract CID from URL (data ID)
            cid_match = re.search(r'!1s(0x[a-f0-9]+:0x[a-f0-9]+)', current_url)
            if cid_match:
                place["cid"] = cid_match.group(1)

            # Extract coordinates from URL
            coord_match = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', current_url)
            if coord_match:
                place["latitude"] = coord_match.group(1)
                place["longitude"] = coord_match.group(2)

            # Extract title (business name) from URL first as it's most reliable
            # URL format: /maps/place/Business+Name/@...
            title_from_url = re.search(r'/maps/place/([^/@]+)', current_url)
            if title_from_url:
                # Decode URL-encoded name
                from urllib.parse import unquote_plus
                place["title"] = unquote_plus(title_from_url.group(1))

            # Also try to get from the page for a cleaner name
            # Look for the h1 in the details panel (not the search header)
            with self._optional(misses, "title_h1"):
                # Wait for the place name to appear in the panel
                await page.wait_for_selector("h1.DUwDvf", timeout=3000)
                title_el = page.locator("h1.DUwDvf").first
                if await title_el.count() > 0:
                    page_title = await title_el.text_content()
                    if page_title and page_title.lower() != "results":
                        place["title"] = page_title.strip()

            # Fallback: try generic h1 in main content area
            if not place["title"] or place["title"].lower() == "results":
                with self._optional(misses, "title_h1_fallback"):
                    title_el = page.locator("div[role='main'] h1").first
                    if await title_el.count() > 0:
                        page_title = await title_el.text_content()
                        if page_title and page_title.lower() != "results":
                            place["title"] = page_title.strip()

            # Extract rating and review count
            rating_el = page.locator("div.F7nice span[aria-hidden='true']").first
            if await rating_el.count() > 0:
                rating_text = await rating_el.text_content()
                try:
                    place["review_rating"] = float(rating_text.replace(",", "."))
                except (ValueError, AttributeError):
                    pass

            # Review count - look for text like "(123)"
            review_count_el = page.locator("div.F7nice span[aria-label*='review']").first
            if await review_count_el.count() > 0:
                rc_text = await review_count_el.get_attribute("aria-label") or ""
                rc_match = re.search(r'([\d,]+)', rc_text.replace(",", ""))
                if rc_match:
                    place["review_count"] = rc_match.group(1)

            # Category
            category_el = page.locator("button[jsaction*='category']").first
            if await category_el.count() > 0:
                place["category"] = await category_el.text_content()

            # Price level
            price_el = page.locator("span[aria-label*='Price']").first
            if await price_el.count() > 0:
                place["price_range"] = await price_el.get_attribute("aria-label")

            # Address - look for data-item-id containing "address"
            addr_el = page.locator("button[data-item-id*='address']").first
            if await addr_el.count() > 0:
                addr_text = await addr_el.get_attribute("aria-label")
                if addr_text:
                    place["address"] = addr_text.replace("Address: ", "")

            # Phone
            phone_el = page.locator("button[data-item-id*='phone']").first
            if await phone_el.count() > 0:
                phone_text = await phone_el.get_attribute("aria-label")
                if phone_text:
                    place["phone"] = phone_text.replace("Phone: ", "")

            # Website
            website_el = page.locator("a[data-item-id='authority']").first
            if await website_el.count() > 0:
                place["website"] = await website_el.get_attribute("href")

            # Plus code
            pluscode_el = page.locator("button[data-item-id*='oloc']").first
            if await pluscode_el.count() > 0:
                pc_text = await pluscode_el.get_attribute("aria-label")
                if pc_text:
                    place["plus_code"] = pc_text.replace("Plus code: ", "")

            # Hours - try to click and expand for full schedule
            with self._optional(misses, "hours"):
                hours_btn = page.locator("button[data-item-id*='oh']").first
                if await hours_btn.count() > 0:
                    # Check if currently open or closed
                    hours_text = await hours_btn.text_content()
                    if hours_text:
                        if "Open" in hours_text:
                            place["is_open_now"] = True
                        elif "Closed" in hours_text:
                            place["is_open_now"] = False

                    # Get the hours text/aria-label
                    hours_label = await hours_btn.get_attribute("aria-label")
                    if hours_label:
                        place["open_hours"] = self._parse_hours_label(hours_label)

                    # Try to click to expand full hours table
                    with self._optional(misses, "hours_expand"):
                        await hours_btn.click()
                        await asyncio.sleep(0.5)

                        # Look for the expanded hours table
                        hours_table = page.locator("table.eK4R0e, table.WgFkxc, div[aria-label*='hours'] table")
                        if await hours_table.count() > 0:
                            expanded_hours = await self._extract_expanded_hours(page)
                            if expanded_hours:
                                place["open_hours"] = expanded_hours

                        # Close the expanded view by pressing Escape
                        await page.keyboard.press("Escape")
                        await asyncio.sleep(0.3)

            # Extract photos from the carousel
            with self._optional(misses, "photos"):
                photo_elements = page.locator("button[jsaction*='heroHeaderImage'] img, div[jsaction*='photo'] img, img.Uf0tqf")
                photo_count = await photo_elements.count()
                for i in range(min(photo_count, 10)):  # Limit to 10 photos
                    photo = photo_elements.nth(i)
                    src = await photo.get_attribute("src")
                    if src and "googleusercontent.com" in src:
                        # Get higher resolution version
                        high_res_src = re.sub(r'=w\d+-h\d+', '=w800-h600', src)
                        place["photos"].append(high_res_src)

            # Menu link
            with self._optional(misses, "menu_link"):
                menu_el = page.locator("a[data-item-id*='menu'], a[aria-label*='Menu']").first
                if await menu_el.count() > 0:
                    place["menu_link"] = await menu_el.get_attribute("href")

            # Order online link
            with self._optional(misses, "order_link"):
                order_el = page.locator("a[data-item-id*='order'], a[aria-label*='Order']").first
                if await order_el.count() > 0:
                    place["order_link"] = await order_el.get_attribute("href")

            # Reserve table link
            with self._optional(misses, "reserve_link"):
                reserve_el = page.locator("a[data-item-id*='reserve'], a[aria-label*='Reserve']").first
                if await reserve_el.count() > 0:
                    place["reserve_link"] = await reserve_el.get_attribute("href")

            # Extract amenities and service options
            with self._optional(misses, "service_options"):
                # Service options (Dine-in, Takeout, Delivery, etc.)
                service_els = page.locator("div[aria-label*='Service options'] span, div[data-tooltip*='Service']")
                service_count = await service_els.count()
                for i in range(service_count):
                    text = await service_els.nth(i).text_content()
                    if text and text.strip():
                        place["service_options"].append(text.strip())

            with self._optional(misses, "accessibility"):
                # Accessibility options
                access_els = page.locator("div[aria-label*='Accessibility'] span, span[aria-label*='Wheelchair']")
                access_count = await access_els.count()
                for i in range(access_count):
                    text = await access_els.nth(i).text_content()
                    if text and text.strip():
                        place["accessibility"].append(text.strip())

            with self._optional(misses, "amenities"):
                # General amenities (from About tab or highlights)
                amenity_els = page.locator("div[aria-label*='Highlights'] span, div[data-attrid*='highlights'] span")
                amenity_count = await amenity_els.count()
                for i in range(amenity_count):
                    text = await amenity_els.nth(i).text_content()
                    if text and text.strip() and len(text.strip()) < 50:
                        place["amenities"].append(text.strip())

            # Extract description/about from the About region
            with self._optional(misses, "description"):
                # Look for the About region button which contains the description
                about_region = page.locator("region[aria-label*='About']")
                if await about_region.count() > 0:
                    about_btn = about_region.locator("button").first
                    if await about_btn.count() > 0:
                        about_text = await about_btn.text_content()
                        if about_text:
                            # Extract just the description part (before service options markers)
                            # Split on common patterns that indicate end of description
                            desc_text = about_text
                            for marker in ["·", "Serves", "Has ", "Dine-in", "Drive-through", "Delivery"]:
                                if marker in desc_text:
                                    desc_text = desc_text.split(marker)[0]
                            desc_text = desc_text.strip()
                            if desc_text and len(desc_text) > 10:
                                place["description"] = desc_text

            # Fallback description extraction
            if not place["description"]:
                with self._optional(misses, "description_fallback"):
                    # Try to find description in various common locations
                    desc_selectors = [
                        "div[data-attrid='description'] span",
                        "div.PYvSYb span",
                        "button:has-text('known for')",
                    ]
                    for selector in desc_selectors:
                        desc_el = page.locator(selector).first
                        if await desc_el.count() > 0:
                            desc = await desc_el.text_content()
                            if desc and len(desc) > 20 and desc.lower() != "learn more":
                                # Clean up the description
                                for marker in ["·", "Serves", "Has "]:
                                    if marker in desc:
                                        desc = desc.split(marker)[0]
                                place["description"] = desc.strip()
                                break

            # Extract price per person
            with self._optional(misses, "price_per_person"):
                price_btn = page.locator("button[aria-label*='per person'], button:has-text('per person')").first
                if await price_btn.count() > 0:
                    price_text = await price_btn.text_content()
                    if price_text:
                        # Extract price range like "$1–10 per person"
                        price_match = re.search(r'\$[\d,]+[–-]\$?[\d,]+', price_text)
                        if price_match:
                            place["price_per_person"] = price_match.group(0)

            # Extract service options (Dine-in, Drive-through, Delivery, etc.)
            with self._optional(misses, "service_option_groups"):
                # Look for service option groups with role="group"
                service_groups = page.locator("[role='group'][aria-label*='Serves'], [role='group'][aria-label*='Has']")
                service_count = await service_groups.count()

                # Try alternative - look for groups in the main panel
                if service_count == 0:
                    service_groups = page.locator("div[role='main'] span[role='group'], div[role='main'] [aria-label*='dine-in'], div[role='main'] [aria-label*='drive-through']")
                    service_count = await service_groups.count()

                for i in range(service_count):
                    label = await service_groups.nth(i).get_attribute("aria-label")
                    if label:
                        # Extract just the service name from "Serves dine-in" or "Has drive-through"
                        if "Serves" in label:
                            service = label.replace("Serves ", "").strip()
                        elif "Has" in label:
                            service = label.replace("Has ", "").strip()
                        else:
                            service = label
                        place["service_options"].append(service.title())

                # Fallback: look for text within the About section
                if not place["service_options"]:
                    about_section = page.locator("region[aria-label*='About']")
                    if await about_section.count() > 0:
                        about_text = await about_section.text_content()
                        if about_text:
                            service_texts = ["Dine-in", "Drive-through", "Takeout", "Delivery", "No-contact delivery", "Curbside pickup"]
                            for svc in service_texts:
                                if svc.lower() in about_text.lower():
                                    place["service_options"].append(svc)

            # Extract popular times data
            with self._optional(misses, "popular_times"):
                popular_times_section = page.locator("region[aria-label*='Popular times'], div:has(heading:has-text('Popular times'))")
                pt_count = await popular_times_section.count()

                # Also try alternative selectors
                busy_imgs_direct = page.locator("img[aria-label*='busy']")
                busy_count_direct = await busy_imgs_direct.count()

                if pt_count > 0 or busy_count_direct > 0:
                    # Get the day selector button
                    day_btn = page.locator("button[aria-label*='days'], button:has-text('Saturdays'), button:has-text('Sundays'), button:has-text('Mondays')").first

                    current_day = "Unknown"
                    if await day_btn.count() > 0:
                        day_text = await day_btn.text_content()
                        if day_text:
                            current_day = day_text.strip()

                    # Get the hourly busy percentages
                    busy_imgs = page.locator("img[aria-label*='busy at'], img[aria-label*='% busy']")
                    img_count = await busy_imgs.count()

                    hourly_data = []
                    for i in range(img_count):
                        label = await busy_imgs.nth(i).get_attribute("aria-label")
                        if label:
                            # Parse "93% busy at 10 AM." or "79% busy at 10 AM"
                            match = re.search(r'(\d+)%\s+busy\s+at\s+(\d+\s*(?:AM|PM))', label, re.IGNORECASE)
                            if match:
                                hourly_data.append({
                                    "hour": match.group(2),
                                    "busy_percent": int(match.group(1))
                                })
                    if hourly_data:
                        place["popular_times"][current_day] = hourly_data

            # Extract live wait time and current busyness
            with self._optional(misses, "live_busyness"):
                # Initialize wait time fields
                place["wait_time_minutes"] = None
                place["wait_time_raw"] = None
                place["live_busyness"] = None
                place["typical_busyness"] = None

                # Look for live busyness indicator (e.g., "Live: Busier than usual")
                live_busy_selectors = [
                    "span:has-text('Live:')",
                    "div:has-text('Busier than usual')",
                    "div:has-text('Less busy than usual')",
                    "div:has-text('As busy as it gets')",
                    "div:has-text('Not too busy')",
                    "[aria-label*='Live']"
                ]

                for selector in live_busy_selectors:
                    live_elem = page.locator(selector).first
                    if await live_elem.count() > 0:
                        live_text = await live_elem.text_content()
                        if live_text and "Live" in live_text:
                            place["live_busyness"] = live_text.strip()
                            break

                # Look for wait time specifically (e.g., "Usually 15 min wait")
                wait_selectors = [
                    "span:has-text('min wait')",
                    "div:has-text('min wait')",
                    "[aria-label*='wait']",
                    "span:has-text('minute wait')"
                ]

                for selector in wait_selectors:
                    wait_elem = page.locator(selector).first
                    if await wait_elem.count() > 0:
                        wait_text = await wait_elem.text_content()
                        if wait_text:
                            # Parse "Usually 15 min wait" or "Live: 20 min wait"
                            match = re.search(r'(\d+)\s*min(?:ute)?\s*wait', wait_text, re.IGNORECASE)
                            if match:
                                place["wait_time_minutes"] = int(match.group(1))
                                place["wait_time_raw"] = wait_text.strip()
                            break

                # Extract typical busyness messages (e.g., "Usually not too busy")
                typical_selectors = [
                    "span:has-text('Usually not too busy')",
                    "span:has-text('Usually a little busy')",
                    "span:has-text('Usually not busy')",
                    "span:has-text('Usually busy')",
                    "div:has-text('Usually')"
                ]

                for selector in typical_selectors:
                    typical_elem = page.locator(selector).first
                    if await typical_elem.count() > 0:
                        typical_text = await typical_elem.text_content()
                        if typical_text and "Usually" in typical_text and "wait" not in typical_text.lower():
                            place["typical_busyness"] = typical_text.strip()
                            break


            # Extract review summary (star breakdown)
            with self._optional(misses, "review_summary"):
                review_table = page.locator("table img[aria-label*='stars']")
                table_count = await review_table.count()

                # Try alternative selector
                if table_count == 0:
                    review_table = page.locator("img[aria-label*='stars'][aria-label*='reviews']")
                    table_count = await review_table.count()

                if table_count > 0:
                    review_summary = {}
                    for i in range(table_count):
                        label = await review_table.nth(i).get_attribute("aria-label")
                        if label:
                            # Parse "5 stars, 474 reviews" or "5 stars, 691 reviews"
                            match = re.search(r'(\d+)\s*stars?,\s*([\d,]+)\s*reviews?', label, re.IGNORECASE)
                            if match:
                                stars = match.group(1)
                                count = match.group(2).replace(",", "")
                                review_summary[f"{stars}_star"] = int(count)
                    if review_summary:
                        place["review_summary"] = review_summary

            # Extract review topics/keywords
            with self._optional(misses, "review_topics"):
                # Look for radio buttons in the review filter section
                topic_radios = page.locator("[role='radio'][aria-label*='mentioned in']")
                topic_count = await topic_radios.count()

                # Try alternative selector
                if topic_count == 0:
                    topic_radios = page.locator("div[role='radio'][aria-label*='reviews'], button[aria-label*='mentioned']")
                    topic_count = await topic_radios.count()

                for i in range(min(topic_count, 10)):  # Limit to 10 topics
                    label = await topic_radios.nth(i).get_attribute("aria-label")
                    if label:
                        # Parse "drive thru, mentioned in 102 reviews"
                        match = re.search(r'([^,]+),\s*mentioned\s+in\s+(\d+)\s+reviews?', label, re.IGNORECASE)
                        if match:
                            place["review_topics"].append({
                                "topic": match.group(1).strip(),
                                "count": int(match.group(2))
                            })

            # Extract sample reviews (quotes shown at top of reviews section)
            with self._optional(misses, "sample_reviews"):
                # Look for buttons containing quoted review text
                all_buttons = page.locator("button")
                btn_count = await all_buttons.count()
                for i in range(min(btn_count, 50)):  # Check first 50 buttons
                    if len(place["sample_reviews"]) >= 3:
                        break
                    try:
                        btn = all_buttons.nth(i)
                        text = await btn.text_content()
                        if text and text.startswith('"') and len(text) > 20:
                            # Clean up the quote
                            clean_text = text.strip('"').strip()
                            if clean_text and clean_text not in place["sample_reviews"]:
                                place["sample_reviews"].append(clean_text)
                    except Exception as exc:
                        # Justified: one detached button out of 50 must not
                        # abort the scan of the other 49. Sample reviews are an
                        # optional field, and the enclosing _optional() records
                        # a wholesale failure of the section.
                        logger.debug("Sample review button %d unreadable: %s", i, exc)
                        continue

            # Extract related places ("People also search for")
            with self._optional(misses, "related_places"):
                # Look for links with aria-label containing stars and reviews
                related_links = page.locator("a[aria-label*='stars'][aria-label*='reviews']")
                related_count = await related_links.count()

                # Skip the first one if it's the current place
                start_idx = 1 if related_count > 1 else 0
                for i in range(start_idx, min(related_count, 6)):  # Limit to 5 related places
                    label = await related_links.nth(i).get_attribute("aria-label")
                    if label:
                        # Parse "Burger King · 3.3 stars · 1,084 reviews · Restaurant"
                        parts = label.split(" · ")
                        if len(parts) >= 3:
                            related = {"name": parts[0]}
                            for part in parts[1:]:
                                if "stars" in part.lower():
                                    rating_match = re.search(r'([\d.]+)', part)
                                    if rating_match:
                                        related["rating"] = float(rating_match.group(1))
                                elif "reviews" in part.lower():
                                    count_match = re.search(r'([\d,]+)', part)
                                    if count_match:
                                        related["review_count"] = int(count_match.group(1).replace(",", ""))
                                else:
                                    related["category"] = part
                            place["related_places"].append(related)

            # Clean up empty lists/dicts
            if not place["photos"]:
                place["photos"] = None
            if not place["amenities"]:
                place["amenities"] = None
            if not place["service_options"]:
                place["service_options"] = None
            if not place["accessibility"]:
                place["accessibility"] = None
            if not place["popular_times"]:
                place["popular_times"] = None
            if not place["review_topics"]:
                place["review_topics"] = None
            if not place["sample_reviews"]:
                place["sample_reviews"] = None
            if not place["related_places"]:
                place["related_places"] = None

            return self._finalise_place(place, misses)

        except ScraperError:
            raise
        except Exception as e:
            # A hard failure here used to become `return None`, which the
            # callers turned into an empty-but-successful result set. Surface
            # it instead; search() decides whether one bad place matters.
            logger.warning("Error extracting place details: %s", e, exc_info=True)
            raise PlaceExtractionError(
                f"Place details extraction raised {type(e).__name__}: {e}",
                missing=list(REQUIRED_PLACE_FIELDS),
            ) from e

    def _finalise_place(
        self, place: dict[str, Any], misses: list[str]
    ) -> dict[str, Any]:
        """Attach freshness metadata and enforce the required fields.

        Args:
            place: The partially populated place dict.
            misses: Optional-field names whose extraction raised.

        Returns:
            ``place``, with ``missing_fields``, ``partial`` and
            ``selectors_stale`` set.

        Raises:
            PlaceExtractionError: When a required field is absent.
        """
        missing_required = [f for f in REQUIRED_PLACE_FIELDS if not place.get(f)]
        if missing_required:
            raise PlaceExtractionError(
                "No required field could be extracted "
                f"(missing: {', '.join(missing_required)}); the place panel "
                "selectors no longer match",
                missing=missing_required,
            )

        # A title is recoverable from the URL alone, so a record carrying a
        # title and nothing else is evidence that the details panel did not
        # parse -- not evidence of a business with no address or phone.
        core_present = [f for f in CORE_PLACE_FIELDS if place.get(f)]
        place["missing_fields"] = sorted(set(misses))
        place["partial"] = not core_present
        place["selectors_stale"] = not core_present

        if not core_present:
            logger.error(
                "Place %r extracted with no core field (%s); treating as a "
                "stale-selector partial result",
                place.get("title"),
                ", ".join(CORE_PLACE_FIELDS),
            )
        elif misses:
            logger.info(
                "Place %r extracted with %d optional field(s) unmatched: %s",
                place.get("title"),
                len(set(misses)),
                ", ".join(sorted(set(misses))),
            )
        else:
            logger.info(
                "Extracted place: %s with %d fields populated",
                place.get("title"),
                len([v for v in place.values() if v]),
            )
        return place

    async def _extract_expanded_hours(self, page) -> dict[str, list[str]] | None:
        """Extract hours from expanded hours table.

        Returns None when no hours row parses -- opening hours are optional and
        plenty of listings have none. A locator that *raises* is deliberately
        not caught here: the caller runs this inside :meth:`_optional`, which
        records the miss, so swallowing it locally would hide it again.
        """
        hours = {}
        # Look for table rows with day and time info
        rows = page.locator("table tr, div[role='listitem']")
        row_count = await rows.count()

        days = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

        for i in range(row_count):
            row = rows.nth(i)
            row_text = await row.text_content()
            if row_text:
                for day in days:
                    if day in row_text:
                        # Extract time portion
                        time_match = re.search(r'(\d{1,2}(?::\d{2})?\s*(?:AM|PM)\s*[–-]\s*\d{1,2}(?::\d{2})?\s*(?:AM|PM)|Closed|Open 24 hours)', row_text, re.IGNORECASE)
                        if time_match:
                            hours[day] = [time_match.group(0)]
                        break

        return hours if hours else None

    def _parse_hours_label(self, label: str) -> dict[str, list[str]] | None:
        """Parse hours from aria-label into structured format."""
        if not label:
            return None

        # Try to parse common patterns like "Monday, 9 AM to 5 PM; Tuesday, 9 AM to 5 PM"
        hours = {}
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

        for day in days:
            if day in label:
                # Find the time for this day
                pattern = rf"{day}[,:]?\s*([^;]+)"
                match = re.search(pattern, label, re.IGNORECASE)
                if match:
                    time_str = match.group(1).strip()
                    # Clean up the time string
                    time_str = re.sub(r'\.\s*$', '', time_str)
                    time_str = time_str.replace(" to ", "–")
                    hours[day] = [time_str]

        return hours if hours else None
