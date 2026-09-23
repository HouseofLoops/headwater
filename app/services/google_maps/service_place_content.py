"""
Per-place content for GoogleMapsService.

Reviews, photos, Q&A, autocomplete, review analytics, batch geocoding,
attributes and history.
"""

import asyncio
import logging
from typing import Any

from app.core.log_safety import scrub
from app.core.proxy import ENABLE_PROXY, proxy_for
from app.core.url_utils import is_googleusercontent_url
from app.services.google_maps.constants import GOOGLE_MAPS_HOST

# Logs under the facade module's name so log routing and filters keyed on
# ``app.services.google_maps_service`` are unaffected by the split.
logger = logging.getLogger("app.services.google_maps_service")


class PlaceContentMixin:
    """Per-place content methods of GoogleMapsService."""

    async def get_place_reviews(
        self,
        place_id: str,
        sort_by: str = "most_relevant",
        limit: int = 50,
        offset: int = 0,
        min_rating: int | None = None,
        include_owner_responses: bool = True,
    ) -> dict[str, Any]:
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
                sample = [r for r in sample if isinstance(r, dict) and (r.get("rating") or 0) >= min_rating]
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
                    None
                    if total_reviews is None
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
        self, place_id: str, max_photos: int = 20, size: str = "large", category: str | None = None
    ) -> dict[str, Any]:
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
            size_map = {"thumbnail": "=w100-h100", "medium": "=w400-h300", "large": "=w800-h600", "original": "=w0"}
            size_suffix = size_map.get(size, "=w800-h600")

            sized_photos = []
            for photo_url in photos[:max_photos]:
                if is_googleusercontent_url(photo_url):
                    # Replace size in URL
                    import re

                    new_url = re.sub(r"=w\d+-h\d+", size_suffix, photo_url)
                    sized_photos.append({"url": new_url})
                else:
                    sized_photos.append({"url": photo_url})

            return {"total_photos": len(photos), "photos": sized_photos}

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

    async def _extract_place_qa(self, page, limit: int, include_answers: bool) -> list[dict[str, Any]] | None:
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

        questions: list[dict[str, Any]] = []
        for node in nodes[:limit]:
            text = " ".join(((await node.inner_text()) or "").split())
            if not text:
                continue
            entry: dict[str, Any] = {"question": text}
            if include_answers:
                answers: list[str] = []
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

    async def get_place_qa(self, place_id: str, limit: int = 20, include_answers: bool = True) -> dict[str, Any]:
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
                "Q&A scrape failed for %s: %s",
                scrub(place_id),
                scrub(e),
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
        types: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        radius_meters: int | None = None,
        language: str = "en",
    ) -> dict[str, Any]:
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
                timeout=30,
            )

            if result.get("error"):
                return result

            predictions = []
            for place in result.get("results", [])[:5]:
                predictions.append(
                    {
                        "description": f"{place.get('title', '')} - {place.get('address', '')}",
                        "place_id": place.get("cid"),
                        "main_text": place.get("title", ""),
                        "secondary_text": place.get("address", ""),
                    }
                )

            return {"predictions": predictions}

        except Exception as e:
            logger.error(f"Autocomplete error: {e}")
            return {"error": True, "message": str(e)}

    async def get_review_analytics(
        self,
        place_id: str,
        time_period: str = "all",
        include_sentiment: bool = True,
        include_trends: bool = True,
        include_keywords: bool = True,
    ) -> dict[str, Any]:
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
                "keywords": place.get("review_topics") or [],
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

    async def batch_geocode(self, addresses: list[str]) -> dict[str, Any]:
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
                    result = await self.search_and_wait(query=address, max_results=1, timeout=30)

                    if result.get("error") or not result.get("results"):
                        failed += 1
                        results.append({"address": address, "success": False, "error": "Address not found"})
                    else:
                        successful += 1
                        place = result["results"][0]
                        results.append(
                            {
                                "address": address,
                                "success": True,
                                "latitude": place.get("latitude"),
                                "longitude": place.get("longitude"),
                                "formatted_address": place.get("address"),
                                "place_id": place.get("cid"),
                            }
                        )

                except Exception as e:
                    failed += 1
                    results.append({"address": address, "success": False, "error": str(e)})

            return {"results": results, "successful": successful, "failed": failed}

        except Exception as e:
            logger.error(f"Geocode error: {e}")
            return {"error": True, "message": str(e)}

    async def get_place_attributes(self, place_id: str) -> dict[str, Any]:
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
                "price_per_person": place.get("price_per_person"),
            }

            return {"attributes": attributes}

        except Exception as e:
            logger.error(f"Get attributes error: {e}")
            return {"error": True, "message": str(e)}

    async def get_place_history(
        self,
        place_id: str,
        field: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
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
