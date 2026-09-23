"""
GoogleMapsScraper: browser lifecycle, search and the results feed.

Place-panel extraction lives in :mod:`.scraper_place_details` and is mixed
in via PlaceDetailsMixin.
"""

import asyncio
import contextlib
import logging
from typing import Any

from app.services.google_maps.scraper_errors import (
    CORE_PLACE_FIELDS,
    REQUIRED_PLACE_FIELDS,
    PlaceExtractionError,
    ScraperError,
    SelectorsStaleError,
)
from app.services.google_maps.scraper_limits import _browser_semaphore
from app.services.google_maps.scraper_place_details import PlaceDetailsMixin

# Logs under the facade module's name so log routing and filters keyed on
# ``app.services.google_maps_scraper`` are unaffected by the split.
logger = logging.getLogger("app.services.google_maps_scraper")


class GoogleMapsScraper(PlaceDetailsMixin):
    """
    Google Maps scraper using Playwright.

    Extracts business data including:
    - Name, address, phone, website
    - Ratings and reviews
    - Operating hours
    - Location coordinates
    - Category and price level
    """

    # Google Maps base URL
    MAPS_URL = "https://www.google.com/maps"
    SEARCH_URL = "https://www.google.com/maps/search/"

    def __init__(self, proxy: str | None = None, headless: bool = True):
        """
        Initialize the scraper.

        Args:
            proxy: Optional proxy URL (e.g., "http://user:pass@host:port")
            headless: Run browser in headless mode
        """
        self.proxy = proxy
        self.headless = headless
        self._browser = None
        self._playwright = None
        self._semaphore: asyncio.Semaphore | None = None
        self._init_lock = asyncio.Lock()
        # Per-search extraction bookkeeping; see _assert_selectors_fresh.
        self._candidates = 0
        self._unverified_empty = False
        self._attempted = 0
        self._extracted = 0
        self._partial = 0
        self._required_misses: list[str] = []

    @contextlib.contextmanager
    def _optional(self, misses: list[str], field_name: str):
        """Run an optional-field extraction, recording rather than hiding misses.

        This replaces 23 bare ``except Exception: pass`` blocks. The behaviour
        for a genuinely absent optional field is unchanged -- extraction
        continues -- but the miss is appended to ``misses`` and logged, so that
        "this restaurant has no menu link" and "the menu selector no longer
        matches anything on any page" stop looking identical.
        """
        try:
            yield
        except Exception as exc:
            misses.append(field_name)
            logger.debug("Optional field %r not extracted: %s", field_name, exc)

    async def _init_browser(self):
        """Initialize Playwright browser, bounded by the global cap.

        The semaphore is held for the whole life of the browser, not just for
        the launch call: what has to be bounded is the number of Chromium
        processes *resident* at once (~100 MB each), not the launch rate. It is
        released in :meth:`close`, which every acquisition path already calls
        from a ``finally``.
        """
        if self._browser is not None:
            return

        from playwright.async_api import async_playwright

        # The check-then-acquire below is not atomic across awaits, so two
        # coroutines sharing one scraper could each take a permit and only one
        # would ever be released. The instance lock makes initialisation
        # single-entry.
        async with self._init_lock:
            if self._browser is not None or self._semaphore is not None:
                return

            semaphore = _browser_semaphore()
            await semaphore.acquire()
            self._semaphore = semaphore
            try:
                await self._launch(async_playwright)
            except BaseException:
                # Never leak a permit on a failed launch, or the cap ratchets
                # down to zero and every later request deadlocks.
                self._semaphore = None
                semaphore.release()
                raise

    def _release_semaphore(self) -> None:
        """Give back the browser permit, at most once."""
        semaphore, self._semaphore = self._semaphore, None
        if semaphore is not None:
            semaphore.release()

    async def _launch(self, async_playwright):
        """Start Playwright and launch Chromium with the configured options."""
        self._playwright = await async_playwright().start()

        # Browser launch options
        launch_options = {
            "headless": self.headless,
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-accelerated-2d-canvas",
                "--disable-gpu",
                "--window-size=1920,1080",
            ],
        }

        # Add proxy if configured
        if self.proxy:
            # Parse proxy URL to extract credentials if present
            # Format: http://user:pass@host:port or http://host:port
            from urllib.parse import urlparse

            parsed = urlparse(self.proxy)

            proxy_config = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}

            if parsed.username and parsed.password:
                proxy_config["username"] = parsed.username
                proxy_config["password"] = parsed.password

            launch_options["proxy"] = proxy_config
            logger.info(f"Using proxy: {parsed.hostname}:{parsed.port}")

        self._browser = await self._playwright.chromium.launch(**launch_options)
        logger.info("Browser initialized successfully")

    async def close(self):
        """Close the browser and release its concurrency permit.

        The permit is released in a ``finally`` so that a browser that fails to
        shut down cleanly still frees its slot; otherwise one hung Chromium
        would permanently shrink the pool.
        """
        try:
            if self._browser:
                await self._browser.close()
                self._browser = None
            if self._playwright:
                await self._playwright.stop()
                self._playwright = None
            logger.info("Browser closed")
        finally:
            self._release_semaphore()

    async def _create_page(self, language: str = "en"):
        """Create a new browser page with appropriate settings."""
        await self._init_browser()

        context = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale=language,
            timezone_id="America/Los_Angeles",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )

        page = await context.new_page()

        # Set extra headers to appear more legitimate
        await page.set_extra_http_headers(
            {
                "Accept-Language": f"{language},en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            }
        )

        return page, context

    async def search(
        self,
        query: str,
        language: str = "en",
        max_results: int = 20,
        zoom: int = 15,
        geo_coordinates: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Search Google Maps for businesses.

        Args:
            query: Search query (e.g., "restaurants in New York")
            language: Language code
            max_results: Maximum number of results to return
            zoom: Map zoom level
            geo_coordinates: Optional coordinates "lat,lng"

        Returns:
            List of place dictionaries
        """
        page, context = await self._create_page(language)
        results = []
        # Reset per-search extraction bookkeeping. ``attempted`` counts places
        # we found a card/link for and tried to open; ``extracted`` counts the
        # ones that yielded a usable record. attempted > 0 with extracted == 0
        # is the fingerprint of a DOM rotation, not of an empty area.
        self._candidates = 0
        self._unverified_empty = False
        self._attempted = 0
        self._extracted = 0
        self._partial = 0
        self._required_misses: list[str] = []

        try:
            # Build search URL
            search_query = query.replace(" ", "+")
            url = f"{self.SEARCH_URL}{search_query}"

            # Add coordinates if provided
            if geo_coordinates:
                try:
                    lat, lng = geo_coordinates.split(",")
                    url += f"/@{lat.strip()},{lng.strip()},{zoom}z"
                except ValueError:
                    logger.warning(f"Invalid geo_coordinates: {geo_coordinates}")

            logger.info(f"Searching Google Maps: {query}")
            logger.info(f"URL: {url}")

            # Navigate to search
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            logger.info("Page loaded (domcontentloaded)")

            # Wait for the page to be fully interactive
            await asyncio.sleep(3)

            # Accept cookies if dialog appears (multiple possible selectors)
            try:
                for selector in [
                    "button:has-text('Accept all')",
                    "button:has-text('Accept')",
                    "button:has-text('Alle akzeptieren')",
                    "[aria-label='Accept all']",
                ]:
                    accept_btn = page.locator(selector)
                    if await accept_btn.count() > 0:
                        await accept_btn.first.click()
                        logger.debug("Clicked cookie consent button")
                        await asyncio.sleep(2)
                        break
            except Exception as e:
                logger.debug(f"No cookie dialog or error: {e}")

            # Wait for results to load - try multiple selectors
            results_loaded = False
            for selector in [
                "div[role='feed']",
                "div[role='main'] div[jsaction*='mouseover']",
                "a[href*='/maps/place/']",
            ]:
                try:
                    await page.wait_for_selector(selector, timeout=10000)
                    results_loaded = True
                    logger.info(f"Found results with selector: {selector}")
                    break
                except Exception as e:
                    logger.info(f"Selector {selector} not found: {e}")
                    continue

            if not results_loaded:
                # Log page content for debugging
                title = await page.title()
                logger.warning(f"No results found. Page title: {title}")
                page_url = page.url
                logger.warning(f"Current URL: {page_url}")
                # Check for blocking/CAPTCHA
                if "sorry" in page_url.lower() or "consent" in page_url.lower():
                    logger.error("Possible blocking or CAPTCHA detected")

            # Check if we have a results list or a single place
            results_feed = page.locator("div[role='feed']")
            feed_count = await results_feed.count()
            logger.info(f"Results feed count: {feed_count}")

            if feed_count > 0:
                # We have a list of results - scroll to load more
                results = await self._extract_search_results(page, max_results)
            else:
                # Check for direct place links
                place_links = page.locator("a[href*='/maps/place/']")
                link_count = await place_links.count()
                logger.info(f"Direct place links count: {link_count}")

                if link_count > 0:
                    # Extract from place links
                    results = await self._extract_from_place_links(page, place_links, max_results)
                else:
                    # Single place result - extract directly
                    self._candidates += 1
                    self._attempted += 1
                    try:
                        place_data = await self._extract_place_details(page)
                    except PlaceExtractionError as exc:
                        self._required_misses.extend(exc.missing)
                        place_data = None
                    if place_data:
                        self._extracted += 1
                        self._partial += bool(place_data.get("partial"))
                        results = [place_data]

            self._assert_selectors_fresh(query, results_loaded)
            logger.info(f"Found {len(results)} places for query: {query}")

        except ScraperError:
            # Already a precise, non-empty-looking failure. Do not downgrade it
            # into a logged-and-swallowed empty result.
            raise
        except Exception as e:
            logger.error(f"Search error: {e}", exc_info=True)
            raise
        finally:
            await context.close()

        return results

    def _assert_selectors_fresh(self, query: str, results_loaded: bool) -> None:
        """Raise if this search's emptiness is a parser failure, not a real zero.

        Two distinguishable situations produce zero places:

        * The area genuinely has no matching businesses. Google renders a
          results container and it is empty, so ``attempted`` is 0 and
          ``results_loaded`` is True. That is a legitimate ``[]``.
        * Google changed its markup. Either no results container matched at all
          (``results_loaded`` False), or cards were found and every single one
          failed to yield a record (``attempted > 0, extracted == 0``).

        Before this check, both returned ``{"success": true, "places": []}``.
        """
        if not results_loaded and self._extracted == 0:
            raise SelectorsStaleError(
                f"No results container matched for {query!r}: none of the known "
                f"result selectors were present, so an empty result cannot be "
                f"distinguished from a markup change or a block page",
                attempted=self._attempted,
                extracted=0,
                missing=["div[role='feed']", "a[href*='/maps/place/']"],
            )

        # ``candidates`` rather than ``attempted``: a card that was visible but
        # never reached extraction -- because its link selector matched
        # nothing, or clicking it threw -- is still evidence that results exist
        # and we cannot read them. Keying off ``attempted`` alone would let a
        # link-selector rotation return a successful empty list.
        if self._candidates > 0 and self._extracted == 0:
            raise SelectorsStaleError(
                f"Found {self._candidates} candidate place(s) for {query!r} but "
                f"extracted none of them ({self._attempted} reached the details "
                f"panel); Google Maps markup has likely changed",
                attempted=self._attempted,
                extracted=self._extracted,
                missing=sorted(set(self._required_misses)) or list(REQUIRED_PLACE_FIELDS),
            )

        # Every place came back title-only. A title is recoverable from the URL
        # without touching the details panel, so this is a parsed-nothing run
        # dressed up as a successful one.
        if self._extracted > 0 and self._partial == self._extracted:
            raise SelectorsStaleError(
                f"All {self._extracted} place(s) for {query!r} were extracted "
                f"without a single core field ({', '.join(CORE_PLACE_FIELDS)}); "
                f"the place details panel selectors are stale",
                attempted=self._attempted,
                extracted=self._extracted,
                missing=list(CORE_PLACE_FIELDS),
            )

    async def _extract_from_place_links(self, page, place_links, max_results: int) -> list[dict[str, Any]]:
        """Extract places from direct place links on the page."""
        results = []
        seen_names = set()
        link_count = await place_links.count()
        # Every link is a candidate; extracting none of them is a stale-selector
        # signal, not an empty result. See _assert_selectors_fresh.
        self._candidates = max(self._candidates, min(link_count, max_results))

        for i in range(min(link_count, max_results)):
            try:
                link = place_links.nth(i)
                name = await link.get_attribute("aria-label") or ""

                if not name or name in seen_names:
                    continue
                seen_names.add(name)

                # Click the link to get details
                await link.click()
                await asyncio.sleep(1.5)

                self._attempted += 1
                try:
                    place_data = await self._extract_place_details(page)
                except PlaceExtractionError as exc:
                    # Recorded, not swallowed: search() turns an all-fail run
                    # into SelectorsStaleError rather than an empty success.
                    self._required_misses.extend(exc.missing)
                    logger.warning("Place link %d failed to extract: %s", i, exc)
                    place_data = None

                if place_data:
                    self._extracted += 1
                    self._partial += bool(place_data.get("partial"))
                    results.append(place_data)
                    logger.debug(f"Extracted: {place_data.get('title', 'Unknown')}")

                # Go back
                await page.go_back()
                await asyncio.sleep(1)

            except Exception as e:
                logger.warning(f"Error extracting link {i}: {e}")
                continue

        return results

    async def _extract_search_results(self, page, max_results: int) -> list[dict[str, Any]]:
        """Extract places from search results list.

        Raises:
            SelectorsStaleError: The feed rendered and contains place links,
                but the card selector matched none of them. Returning ``[]``
                here would report a card-markup change as an empty area.
        """
        results = []
        seen_names = set()
        scroll_count = 0
        max_scrolls = max(5, max_results // 4)  # Estimate scrolls needed

        while len(results) < max_results and scroll_count < max_scrolls:
            # Find all place cards in the feed
            place_cards = page.locator("div[role='feed'] > div > div[jsaction]")
            card_count = await place_cards.count()

            if card_count == 0 and not results:
                # The feed exists but our card selector sees nothing in it.
                # Place links are how a result manifests regardless of the
                # surrounding card markup, so they discriminate the two cases:
                # links present means our selector went stale; no links means
                # the area really is empty.
                stray_links = await page.locator("a[href*='/maps/place/']").count()
                if stray_links > 0:
                    raise SelectorsStaleError(
                        f"The results feed contains {stray_links} place link(s) "
                        f"but the card selector matched none of them; the "
                        f"result-card markup has changed",
                        attempted=0,
                        extracted=0,
                        missing=["div[role='feed'] > div > div[jsaction]"],
                    )

                # Neither cards nor place links matched inside a feed that did
                # render. This is genuinely ambiguous -- an empty area looks
                # exactly like a wholesale markup rotation -- so raising would
                # turn every legitimately empty search into a 503. Instead the
                # emptiness is labelled as unverified and carried out to the
                # caller, so it is never presented as a *confirmed* zero.
                self._unverified_empty = True
                logger.error(
                    "Results feed rendered but neither the card selector nor "
                    "any place link matched; returning an EMPTY result that "
                    "could not be verified as a genuine zero"
                )
                break

            # Candidates are cards we can see. If we can see candidates and
            # extract nothing from any of them, search() treats that as stale
            # rather than as an empty result -- see _assert_selectors_fresh.
            self._candidates = max(self._candidates, card_count)

            for i in range(card_count):
                if len(results) >= max_results:
                    break

                try:
                    card = place_cards.nth(i)

                    # Get the link/anchor element
                    link = card.locator("a[href*='/maps/place/']").first
                    if await link.count() == 0:
                        continue

                    # Extract name from aria-label or text
                    name = await link.get_attribute("aria-label") or ""
                    if not name:
                        continue

                    # Skip duplicates
                    if name in seen_names:
                        continue
                    seen_names.add(name)

                    # Click to get details
                    await link.click()
                    await asyncio.sleep(1.5)  # Wait for details panel

                    # Extract detailed info
                    self._attempted += 1
                    try:
                        place_data = await self._extract_place_details(page)
                    except PlaceExtractionError as exc:
                        # Recorded, not swallowed: search() turns an all-fail
                        # run into SelectorsStaleError, not an empty success.
                        self._required_misses.extend(exc.missing)
                        logger.warning("Card %d failed to extract: %s", i, exc)
                        place_data = None

                    if place_data:
                        self._extracted += 1
                        self._partial += bool(place_data.get("partial"))
                        results.append(place_data)
                        logger.debug(f"Extracted: {place_data.get('title', 'Unknown')}")

                    # Go back to results
                    await page.go_back()
                    await asyncio.sleep(1)

                except Exception as e:
                    logger.warning(f"Error extracting card {i}: {e}")
                    continue

            # Scroll to load more results
            scroll_count += 1
            try:
                feed = page.locator("div[role='feed']")
                await feed.evaluate("el => el.scrollTop = el.scrollHeight")
                await asyncio.sleep(1.5)
            except Exception as exc:
                # Justified broad catch: a scroll failure means "no more
                # results to load", which is a legitimate stop condition. It is
                # logged rather than passed, and it cannot manufacture a
                # successful empty result -- search() still checks whether the
                # places already attempted actually extracted.
                logger.info("Stopped scrolling the results feed: %s", exc)
                break

        return results
