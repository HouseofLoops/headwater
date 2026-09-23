"""Tests for the de-fabricated Google Maps service endpoints.

Each test here pins down one endpoint that used to return invented,
success-shaped data:

- ``get_directions`` returned a haversine great-circle distance divided by a
  hard-coded speed table (``{"driving": 50, ...}``) and presented it as a
  route. The tests assert the returned numbers come from the page, and that a
  scrape failure is an error rather than a fallback to that estimate.
- ``extract_menu``, ``check_availability`` and ``get_place_qa`` returned an
  empty list plus a ``message`` no client reads, so "we did not look" and "there
  is genuinely nothing" were the same response. The tests assert those states
  are now distinguishable.
- ``get_streetview`` is gone; a test asserts it stays gone.
- The monitor and webhook methods now delegate to durable owner-scoped storage.

No browser: Playwright is replaced by a page double whose selector results the
test declares.
"""

import socket
from typing import ClassVar
from unittest.mock import AsyncMock, patch

import pytest

from app.services.google_maps_service import GoogleMapsService, google_maps_service
from app.services.record_store import RecordStore

# --------------------------------------------------------------------------
# Playwright doubles
# --------------------------------------------------------------------------


class FakeNode:
    def __init__(self, text="", children=None):
        self._text = text
        self._children = children or {}

    async def inner_text(self):
        return self._text

    async def query_selector_all(self, selector):
        return self._children.get(selector, [])


class FakePage:
    """A page whose ``query_selector`` results are declared by the test.

    ``selectors`` maps a CSS selector to the list of nodes it matches. A
    selector that is absent matches nothing, which is how "Google changed its
    markup" is simulated.
    """

    def __init__(self, selectors=None, goto_error=None):
        self.selectors = selectors or {}
        self.goto_error = goto_error
        self.visited = []

    async def goto(self, url, **kwargs):
        self.visited.append(url)
        if self.goto_error:
            raise self.goto_error

    async def query_selector_all(self, selector):
        return self.selectors.get(selector, [])

    async def query_selector(self, selector):
        matches = self.selectors.get(selector, [])
        return matches[0] if matches else None


class FakeContext:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class FakeScraper:
    """Stands in for GoogleMapsScraper. ``page`` is set by each test."""

    page = None
    instances: ClassVar[list] = []

    def __init__(self, *args, **kwargs):
        self.closed = False
        self.context = FakeContext()
        FakeScraper.instances.append(self)

    async def _create_page(self, language="en"):
        return FakeScraper.page, self.context

    async def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def fast_and_offline(monkeypatch):
    """No real sleeps, no real browser, no real Redis, no real DNS."""
    import app.core.url_guard as url_guard
    import app.services.google_maps_scraper as scraper_module
    from app.services import record_store

    record_store._stores.clear()
    FakeScraper.instances = []
    FakeScraper.page = FakePage()

    monkeypatch.setattr(GoogleMapsService, "PAGE_SETTLE_SECONDS", 0)
    monkeypatch.setattr(GoogleMapsService, "DIRECTIONS_SETTLE_SECONDS", 0)
    monkeypatch.setattr(scraper_module, "GoogleMapsScraper", FakeScraper)
    monkeypatch.setattr(
        url_guard.socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))],
    )

    with patch.object(RecordStore, "_get_redis", return_value=None):
        yield

    record_store._stores.clear()


@pytest.fixture
def service():
    return GoogleMapsService()


def route_card(text):
    return FakeNode(text)


DIRECTIONS_SELECTOR = 'div[id^="section-directions-trip-"]'
STEP_SELECTOR = 'div[class*="directions-mode-step"]'
MENU_ITEM_SELECTOR = 'div[jsaction*="dish"]'
MENU_SECTION_SELECTOR = 'div[role="region"][aria-label*="Menu"]'
RESERVE_MODULE_SELECTOR = '[data-item-id="reserve"]'
RESERVE_SLOT_SELECTOR = 'div[jsaction*="reserve"] button[aria-label*=":"]'
QA_SECTION_SELECTOR = 'div[aria-label*="Questions and answers"]'
QA_ITEM_SELECTOR = 'div[aria-label*="Questions and answers"] div[role="listitem"]'


# --------------------------------------------------------------------------
# Street View is deleted
# --------------------------------------------------------------------------


class TestStreetViewRemoved:
    def test_method_no_longer_exists(self):
        """The owner confirmed Street View is not needed; no stub was left."""
        assert not hasattr(google_maps_service, "get_streetview")
        assert not hasattr(GoogleMapsService, "get_streetview")


# --------------------------------------------------------------------------
# Directions
# --------------------------------------------------------------------------


@pytest.mark.asyncio
class TestDirections:
    async def test_returns_the_values_rendered_on_the_page(self, service):
        FakeScraper.page = FakePage(
            {
                DIRECTIONS_SELECTOR: [route_card("via I-280 N\n38 min\n27.4 km")],
                STEP_SELECTOR: [FakeNode("Head north on Main St\n400 m")],
            }
        )

        result = await service.get_directions(37.77, -122.41, 37.33, -121.88)

        assert not result.get("error")
        route = result["routes"][0]
        assert route["distance"] == "27.4 km"
        assert route["distance_meters"] == 27400
        assert route["duration_seconds"] == 38 * 60
        assert route["summary"] == "via I-280 N"
        assert result["source"] == "google_maps_scrape"
        assert route["steps"][0]["instruction"] == "Head north on Main St 400 m"
        assert route["steps_available"] is True

    async def test_the_haversine_speed_table_estimate_is_gone(self, service):
        """Regression guard on the specific fabrication that was removed.

        The old code returned distance = great-circle km and duration =
        distance / a hard-coded speed (driving 50 km/h). For these coordinates
        that produced roughly 67 km and 80 minutes regardless of any road. The
        scraped values must be what the page said, not that.
        """
        FakeScraper.page = FakePage({DIRECTIONS_SELECTOR: [route_card("via US-101 S\n1 hr 5 min\n77.2 km")]})

        result = await service.get_directions(37.77, -122.41, 37.33, -121.88)

        route = result["routes"][0]
        assert route["distance_meters"] == 77200
        assert route["duration_seconds"] == 65 * 60
        # And nothing in the payload advertises itself as an approximation.
        assert "message" not in route

    async def test_scrape_failure_returns_an_error_not_an_estimate(self, service):
        """No route card rendered -> 502, never a computed guess."""
        FakeScraper.page = FakePage({})

        result = await service.get_directions(37.77, -122.41, 37.33, -121.88)

        assert result["error"] is True
        assert result["status_code"] == 502
        assert "routes" not in result

    async def test_navigation_failure_returns_an_error(self, service):
        FakeScraper.page = FakePage(
            {DIRECTIONS_SELECTOR: [route_card("via X\n10 min\n5 km")]},
            goto_error=RuntimeError("net::ERR_TIMED_OUT"),
        )

        result = await service.get_directions(1.0, 2.0, 3.0, 4.0)

        assert result["error"] is True
        assert result["status_code"] == 502
        assert "routes" not in result

    async def test_missing_steps_are_reported_not_implied(self, service):
        """steps: [] with steps_available False, so 'no turns' is not claimed."""
        FakeScraper.page = FakePage({DIRECTIONS_SELECTOR: [route_card("via A\n12 min\n8 km")]})

        result = await service.get_directions(1.0, 2.0, 3.0, 4.0)

        assert result["routes"][0]["steps_available"] is False
        assert result["routes"][0]["steps"] == []

    async def test_alternatives_returns_every_rendered_route(self, service):
        FakeScraper.page = FakePage(
            {
                DIRECTIONS_SELECTOR: [
                    route_card("via I-280 N\n38 min\n27.4 km"),
                    route_card("via US-101 N\n42 min\n25.1 km"),
                ]
            }
        )

        one = await service.get_directions(1.0, 2.0, 3.0, 4.0, alternatives=False)
        many = await service.get_directions(1.0, 2.0, 3.0, 4.0, alternatives=True)

        assert len(one["routes"]) == 1
        assert len(many["routes"]) == 2

    async def test_alternative_routes_do_not_borrow_the_selected_routes_steps(self, service):
        """Google renders steps for the selected route only.

        Copying them onto every alternative would invent turns for routes we
        never looked at -- the same fabrication class as the old estimate.
        """
        FakeScraper.page = FakePage(
            {
                DIRECTIONS_SELECTOR: [
                    route_card("via I-280 N\n38 min\n27.4 km"),
                    route_card("via US-101 N\n42 min\n25.1 km"),
                ],
                STEP_SELECTOR: [FakeNode("Merge onto I-280 N\n2.0 km")],
            }
        )

        result = await service.get_directions(1.0, 2.0, 3.0, 4.0, alternatives=True)

        assert result["routes"][0]["steps_available"] is True
        assert len(result["routes"][0]["steps"]) == 1
        assert result["routes"][1]["steps_available"] is False
        assert result["routes"][1]["steps"] == []

    async def test_unknown_travel_mode_is_rejected(self, service):
        result = await service.get_directions(1.0, 2.0, 3.0, 4.0, mode="teleport")
        assert result["error"] is True
        assert result["status_code"] == 400

    async def test_mode_and_avoid_reach_the_url(self, service):
        page = FakePage({DIRECTIONS_SELECTOR: [route_card("via A\n9 min\n2 km")]})
        FakeScraper.page = page

        await service.get_directions(1.0, 2.0, 3.0, 4.0, mode="walking", avoid=["tolls", "junk"])

        url = page.visited[0]
        assert "travelmode=walking" in url
        assert "avoid=tolls" in url
        assert "junk" not in url

    async def test_a_non_route_element_is_not_emitted_as_a_route(self, service):
        """A selector that matched some unrelated element must not become a route."""
        FakeScraper.page = FakePage({DIRECTIONS_SELECTOR: [route_card("Send directions to your phone")]})

        result = await service.get_directions(1.0, 2.0, 3.0, 4.0)

        assert result["error"] is True
        assert result["status_code"] == 502

    async def test_browser_is_always_closed(self, service):
        FakeScraper.page = FakePage({})
        await service.get_directions(1.0, 2.0, 3.0, 4.0)
        assert FakeScraper.instances[-1].closed is True
        assert FakeScraper.instances[-1].context.closed is True


class TestDirectionParsers:
    """Unit coverage for the text parsing the scrape depends on."""

    @pytest.mark.parametrize(
        "text,seconds",
        [
            ("38 min", 2280),
            ("1 hr 5 min", 3900),
            ("2 hr", 7200),
            ("1 day 3 hr", 97200),
            ("no time here", None),
        ],
    )
    def test_duration(self, text, seconds):
        assert GoogleMapsService._parse_duration(text) == seconds

    @pytest.mark.parametrize(
        "text,metres",
        [
            ("27.4 km", 27400),
            ("850 m", 850),
            ("3.1 mi", 4989),
            ("1,204 km", 1204000),
        ],
    )
    def test_distance(self, text, metres):
        _label, value = GoogleMapsService._parse_distance(text)
        assert round(value) == metres

    def test_distance_absent(self):
        assert GoogleMapsService._parse_distance("no distance") is None


# --------------------------------------------------------------------------
# Menus
# --------------------------------------------------------------------------


@pytest.mark.asyncio
class TestExtractMenu:
    def _place(self, **overrides):
        data = {"google_maps_url": "https://www.google.com/maps/place/x", "menu_link": None}
        data.update(overrides)
        return {"place": data}

    async def test_scraped_menu_items(self, service):
        FakeScraper.page = FakePage(
            {
                MENU_SECTION_SELECTOR: [FakeNode("Menu")],
                MENU_ITEM_SELECTOR: [
                    FakeNode("Flat White\n$4.50\nDouble ristretto, steamed milk"),
                    FakeNode("Croissant\n$3.00"),
                ],
            }
        )
        with patch.object(service, "get_place_by_id", AsyncMock(return_value=self._place())):
            result = await service.extract_menu("p1")

        assert result["menu_status"] == "scraped"
        assert result["menu"][0]["name"] == "Flat White"
        assert result["menu"][0]["price"] == "$4.50"
        assert result["menu"][0]["description"] == "Double ristretto, steamed milk"
        assert result["categories"] == {"Menu": result["menu"]}

    async def test_include_flags_are_honoured(self, service):
        FakeScraper.page = FakePage({MENU_ITEM_SELECTOR: [FakeNode("Flat White\n$4.50\nDouble ristretto")]})
        with patch.object(service, "get_place_by_id", AsyncMock(return_value=self._place())):
            result = await service.extract_menu(
                "p1", include_prices=False, include_descriptions=False, categorize=False
            )

        item = result["menu"][0]
        assert "price" not in item
        assert "description" not in item
        assert result["categories"] == {}

    async def test_genuinely_no_menu_is_distinct_from_could_not_read(self, service):
        """No menu link and no menu section: a legitimate empty result."""
        FakeScraper.page = FakePage({})
        with patch.object(service, "get_place_by_id", AsyncMock(return_value=self._place())):
            result = await service.extract_menu("p1")

        assert result["menu_status"] == "no_menu"
        assert result["menu_available"] is False

    async def test_external_menu_is_not_reported_as_an_empty_menu(self, service):
        FakeScraper.page = FakePage({})
        with patch.object(
            service,
            "get_place_by_id",
            AsyncMock(return_value=self._place(menu_link="https://doordash.example/menu")),
        ):
            result = await service.extract_menu("p1")

        assert result["menu_status"] == "external_menu_not_scraped"
        assert result["menu_available"] is True
        assert result["menu_link"] == "https://doordash.example/menu"
        assert result["menu"] == []

    async def test_unreadable_menu_is_reported_as_our_failure(self, service):
        """A menu section rendered but no items parsed: we failed, not the place."""
        FakeScraper.page = FakePage({MENU_SECTION_SELECTOR: [FakeNode("Menu")]})
        with patch.object(service, "get_place_by_id", AsyncMock(return_value=self._place())):
            result = await service.extract_menu("p1")

        assert result["menu_status"] == "unreadable"
        assert result["menu_available"] is True

    async def test_page_load_failure_is_an_error(self, service):
        FakeScraper.page = FakePage({}, goto_error=RuntimeError("net::ERR_ABORTED"))
        with patch.object(service, "get_place_by_id", AsyncMock(return_value=self._place())):
            result = await service.extract_menu("p1")

        assert result["error"] is True
        assert result["status_code"] == 502

    async def test_place_lookup_failure_propagates(self, service):
        with patch.object(service, "get_place_by_id", AsyncMock(return_value={"error": True, "message": "nope"})):
            result = await service.extract_menu("p1")
        assert result["error"] is True


# --------------------------------------------------------------------------
# Reservation availability
# --------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCheckAvailability:
    def _place(self, **overrides):
        data = {"google_maps_url": "https://www.google.com/maps/place/x", "reserve_link": None}
        data.update(overrides)
        return {"place": data}

    async def test_slots_are_read_from_the_page(self, service):
        FakeScraper.page = FakePage(
            {
                RESERVE_MODULE_SELECTOR: [FakeNode("Reserve")],
                RESERVE_SLOT_SELECTOR: [FakeNode("6:30 PM"), FakeNode("7:00 PM"), FakeNode("7:00 PM")],
            }
        )
        with patch.object(
            service, "get_place_by_id", AsyncMock(return_value=self._place(reserve_link="https://r.example"))
        ):
            result = await service.check_availability("p1", "2026-09-10", 2)

        assert result["availability_status"] == "slots_found"
        assert result["time_slots"] == ["6:30 PM", "7:00 PM"]
        # Honest about the filters we could not apply.
        assert result["filters_applied"] is False
        assert result["requested_party_size"] == 2

    async def test_no_reservation_integration_is_stated_explicitly(self, service):
        FakeScraper.page = FakePage({})
        with patch.object(service, "get_place_by_id", AsyncMock(return_value=self._place())):
            result = await service.check_availability("p1", "2026-09-10", 2)

        assert result["availability_status"] == "no_reservation_integration"
        assert result["reservations_available"] is False

    async def test_external_provider_is_not_reported_as_fully_booked(self, service):
        FakeScraper.page = FakePage({})
        with patch.object(
            service,
            "get_place_by_id",
            AsyncMock(return_value=self._place(reserve_link="https://opentable.example/x")),
        ):
            result = await service.check_availability("p1", "2026-09-10", 2)

        assert result["availability_status"] == "external_provider"
        assert result["reservations_available"] is True
        assert result["booking_url"] == "https://opentable.example/x"
        assert "fully booked" in result["message"]

    async def test_module_with_no_slots_is_a_real_negative(self, service):
        FakeScraper.page = FakePage({RESERVE_MODULE_SELECTOR: [FakeNode("Reserve a table")]})
        with patch.object(
            service, "get_place_by_id", AsyncMock(return_value=self._place(reserve_link="https://r.example"))
        ):
            result = await service.check_availability("p1", "2026-09-10", 2)

        assert result["availability_status"] == "no_slots"

    async def test_page_load_failure_is_an_error(self, service):
        FakeScraper.page = FakePage({}, goto_error=RuntimeError("boom"))
        with patch.object(service, "get_place_by_id", AsyncMock(return_value=self._place())):
            result = await service.check_availability("p1", "2026-09-10", 2)
        assert result["error"] is True
        assert result["status_code"] == 502


# --------------------------------------------------------------------------
# Q&A (same fabrication class, found while working in this file)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPlaceQA:
    def _place(self):
        return {"place": {"google_maps_url": "https://www.google.com/maps/place/x"}}

    async def test_questions_are_scraped(self, service):
        FakeScraper.page = FakePage(
            {
                QA_SECTION_SELECTOR: [FakeNode("Questions and answers")],
                QA_ITEM_SELECTOR: [FakeNode("Is there parking?")],
            }
        )
        with patch.object(service, "get_place_by_id", AsyncMock(return_value=self._place())):
            result = await service.get_place_qa("p1")

        assert result["qa_status"] == "scraped"
        assert result["total_questions"] == 1
        assert result["questions"][0]["question"] == "Is there parking?"

    async def test_absent_section_is_not_reported_as_zero_questions(self, service):
        """The old code returned total_questions: 0 without ever looking."""
        FakeScraper.page = FakePage({})
        with patch.object(service, "get_place_by_id", AsyncMock(return_value=self._place())):
            result = await service.get_place_qa("p1")

        assert result["qa_status"] == "not_rendered"
        assert "unknown" in result["message"].lower()
        # None, not 0: a count of zero next to "we could not see it" is the
        # half a client reads, and it is the half that is untrue.
        assert result["total_questions"] is None

    async def test_empty_section_is_a_real_zero(self, service):
        FakeScraper.page = FakePage({QA_SECTION_SELECTOR: [FakeNode("Questions and answers")]})
        with patch.object(service, "get_place_by_id", AsyncMock(return_value=self._place())):
            result = await service.get_place_qa("p1")

        assert result["qa_status"] == "no_questions"
        assert result["total_questions"] == 0


# --------------------------------------------------------------------------
# Monitors and webhooks through the service layer
# --------------------------------------------------------------------------


@pytest.mark.asyncio
class TestServiceMonitorDelegation:
    async def test_created_monitor_can_be_fetched_back(self, service):
        """The original bug, at the layer the router calls."""
        created = await service.create_monitor(place_id="p1", api_key="key-a")
        assert created["status"] == "active"

        fetched = await service.get_monitor(created["monitor_id"], api_key="key-a")
        assert not fetched.get("error")
        assert fetched["monitor"]["monitor_id"] == created["monitor_id"]

    async def test_list_monitors_returns_what_was_created(self, service):
        await service.create_monitor(place_id="p1", api_key="key-a")
        listing = await service.list_monitors(api_key="key-a")
        assert listing["total"] == 1
        assert "message" not in listing  # the old "storage not implemented" is gone

    async def test_a_different_api_key_gets_a_404(self, service):
        created = await service.create_monitor(place_id="p1", api_key="key-a")
        other = await service.get_monitor(created["monitor_id"], api_key="key-b")
        assert other["error"] is True
        assert other["status_code"] == 404

    async def test_a_different_api_key_cannot_delete(self, service):
        created = await service.create_monitor(place_id="p1", api_key="key-a")
        assert (await service.delete_monitor(created["monitor_id"], api_key="key-b"))["status_code"] == 404
        assert not (await service.get_monitor(created["monitor_id"], api_key="key-a")).get("error")

    async def test_delete_then_get_is_404(self, service):
        created = await service.create_monitor(place_id="p1", api_key="key-a")
        assert (await service.delete_monitor(created["monitor_id"], api_key="key-a"))["deleted"] is True
        assert (await service.get_monitor(created["monitor_id"], api_key="key-a"))["status_code"] == 404

    async def test_missing_target_is_a_400(self, service):
        result = await service.create_monitor(api_key="key-a")
        assert result["error"] is True
        assert result["status_code"] == 400

    async def test_ssrf_webhook_url_on_a_monitor_is_a_400(self, service):
        with patch(
            "app.core.url_guard.socket.getaddrinfo",
            lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))],
        ):
            result = await service.create_monitor(
                place_id="p1", webhook_url="https://evil.example/hook", api_key="key-a"
            )
        assert result["error"] is True
        assert result["status_code"] == 400


@pytest.mark.asyncio
class TestServiceWebhookDelegation:
    async def test_registered_webhook_is_listed(self, service):
        registered = await service.register_webhook(
            url="https://hooks.example.com/h", events=["monitor.changed"], api_key="key-a"
        )
        assert registered["webhook_id"]
        listing = await service.list_webhooks(api_key="key-a")
        assert listing["total"] == 1
        assert "message" not in listing

    async def test_webhooks_are_owner_isolated(self, service):
        registered = await service.register_webhook(
            url="https://hooks.example.com/h", events=["monitor.changed"], api_key="key-a"
        )
        assert (await service.list_webhooks(api_key="key-b"))["webhooks"] == []
        assert (await service.delete_webhook(registered["webhook_id"], api_key="key-b"))["status_code"] == 404
        assert (await service.list_webhooks(api_key="key-a"))["total"] == 1

    async def test_ssrf_target_is_a_400(self, service):
        with patch(
            "app.core.url_guard.socket.getaddrinfo",
            lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port))],
        ):
            result = await service.register_webhook(
                url="https://metadata.example/h", events=["monitor.changed"], api_key="key-a"
            )
        assert result["error"] is True
        assert result["status_code"] == 400


@pytest.mark.asyncio
class TestServicePlaceHistory:
    async def test_unmonitored_place_says_so(self, service):
        result = await service.get_place_history("p1", api_key="key-a")
        assert result["monitored"] is False

    async def test_monitored_place_reports_monitored(self, service):
        await service.create_monitor(place_id="p1", api_key="key-a")
        result = await service.get_place_history("p1", api_key="key-a")
        assert result["monitored"] is True

    async def test_bad_date_is_a_400(self, service):
        await service.create_monitor(place_id="p1", api_key="key-a")
        result = await service.get_place_history("p1", start_date="whenever", api_key="key-a")
        assert result["error"] is True
        assert result["status_code"] == 400


# --------------------------------------------------------------------------
# The two endpoints the review over-counted: verified real, left alone
# --------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAlreadyRealEndpoints:
    async def test_get_place_attributes_reflects_scraped_fields(self, service):
        scraped = {
            "place": {
                "service_options": ["Dine-in"],
                "accessibility": ["Wheelchair accessible entrance"],
                "amenities": ["Wi-Fi"],
                "description": "Cosy",
                "price_level": "$$",
                "price_per_person": "$10-20",
            }
        }
        with patch.object(service, "get_place_by_id", AsyncMock(return_value=scraped)):
            result = await service.get_place_attributes("p1")

        assert result["attributes"]["service_options"] == ["Dine-in"]
        assert result["attributes"]["amenities"] == ["Wi-Fi"]

    async def test_batch_geocode_uses_real_search_results(self, service):
        with patch.object(
            service,
            "search_and_wait",
            AsyncMock(return_value={"results": [{"latitude": 1.5, "longitude": 2.5, "address": "A", "cid": "c"}]}),
        ):
            result = await service.batch_geocode(["1 Test St"])

        assert result["successful"] == 1
        assert result["results"][0]["latitude"] == 1.5

    async def test_batch_geocode_reports_a_miss_as_a_failure(self, service):
        with patch.object(service, "search_and_wait", AsyncMock(return_value={"results": []})):
            result = await service.batch_geocode(["nowhere"])

        assert result["failed"] == 1
        assert result["results"][0]["success"] is False


@pytest.mark.asyncio
class TestReviewPagination:
    async def test_has_more_is_computed_not_hard_coded_false(self, service):
        """The old code always claimed has_more: False regardless of the count."""
        scraped = {"place": {"review_count": 100, "reviews": [{"text": "a"}], "rating": 4.0}}
        with patch.object(service, "get_place_by_id", AsyncMock(return_value=scraped)):
            result = await service.get_place_reviews("p1")
        assert result["has_more"] is True

    async def test_has_more_false_when_the_sample_is_complete(self, service):
        scraped = {"place": {"review_count": 1, "reviews": [{"text": "a"}], "rating": 4.0}}
        with patch.object(service, "get_place_by_id", AsyncMock(return_value=scraped)):
            result = await service.get_place_reviews("p1")
        assert result["has_more"] is False

    async def test_requested_filters_are_applied_or_declared_unapplied(self, service):
        """Silently ignoring a filter lets a caller believe they got a filtered page."""
        scraped = {
            "place": {
                "review_count": 3,
                "rating": 4.0,
                "reviews": [{"rating": 5}, {"rating": 2}, {"rating": 4}],
            }
        }
        with patch.object(service, "get_place_by_id", AsyncMock(return_value=scraped)):
            result = await service.get_place_reviews("p1", min_rating=4, limit=1, sort_by="newest")

        assert [r["rating"] for r in result["reviews"]] == [5]
        assert result["filters_applied"]["min_rating"] is True
        assert result["filters_applied"]["limit"] is True
        # Declared unapplied rather than quietly dropped.
        assert result["filters_applied"]["sort_by"] is False
        assert result["filters_applied"]["include_owner_responses"] is False

    async def test_offset_pages_within_the_sample(self, service):
        scraped = {"place": {"review_count": 3, "rating": 4.0, "reviews": [{"rating": 5}, {"rating": 3}]}}
        with patch.object(service, "get_place_by_id", AsyncMock(return_value=scraped)):
            result = await service.get_place_reviews("p1", offset=1)
        assert [r["rating"] for r in result["reviews"]] == [3]

    async def test_unknown_review_count_is_none_not_zero(self, service):
        """'We could not read the total' is not 'this place has no reviews'."""
        scraped = {"place": {"review_count": None, "reviews": [], "rating": None}}
        with patch.object(service, "get_place_by_id", AsyncMock(return_value=scraped)):
            result = await service.get_place_reviews("p1")
        assert result["total_reviews"] is None
        assert result["has_more"] is None
