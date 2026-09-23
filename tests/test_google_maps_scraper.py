"""Tests for the native Google Maps scraper.

No real network and no real browser: Playwright's ``page``/``browser`` and the
Redis client are replaced with fakes that let each defect be reproduced
deterministically.

Three classes of defect are covered, each of which was previously untested:

* **Cross-tenant job access.** ``JobStore`` had no owner parameter, so any
  valid API key could read, list and delete every other caller's jobs.
* **Unbounded browser fan-out.** Nothing capped concurrent Chromium instances
  (~100 MB each); an 11x11 grid search meant 121 of them.
* **Failures reported as empty success.** ``except Exception: pass`` around
  every selector turned a Google DOM rotation into
  ``{"success": true, "places": []}``.
"""

import asyncio
import fnmatch
from unittest.mock import patch

import pytest

from app.services.google_maps_scraper import (
    CORE_PLACE_FIELDS,
    GoogleMapsScraper,
    JobStatus,
    JobStore,
    PlaceExtractionError,
    ScrapeJob,
    SelectorsStaleError,
    cap_fanout,
    configure_limits,
    get_max_concurrent_browsers,
    get_max_fanout,
    run_scrape_job,
)
from app.services.record_store import RecordStore

# --------------------------------------------------------------------------
# Playwright fakes
# --------------------------------------------------------------------------


class FakeTimeoutError(Exception):
    """Stands in for playwright's TimeoutError."""


class FakeLocator:
    """A Playwright locator over a fixed list of element specs.

    An element spec is a dict with optional ``text``, ``attrs`` and
    ``children`` (a selector -> specs mapping). ``raises`` makes ``count()``
    throw, which is how a selector that blows up (rather than merely matching
    nothing) is simulated.
    """

    def __init__(self, selector, specs, page=None):
        self.selector = selector
        self._specs = list(specs)
        self._page = page

    async def count(self):
        for spec in self._specs:
            if spec.get("raises"):
                raise RuntimeError(f"locator {self.selector!r} exploded")
        return len(self._specs)

    @property
    def first(self):
        return FakeLocator(self.selector, self._specs[:1], self._page)

    def nth(self, index):
        return FakeLocator(self.selector, self._specs[index : index + 1], self._page)

    def locator(self, selector):
        specs = []
        for spec in self._specs:
            specs.extend(spec.get("children", {}).get(selector, []))
        return FakeLocator(selector, specs, self._page)

    async def text_content(self):
        return self._specs[0].get("text") if self._specs else None

    async def get_attribute(self, name):
        if not self._specs:
            return None
        return self._specs[0].get("attrs", {}).get(name)

    async def click(self):
        if self._page is not None:
            self._page.clicks.append(self.selector)

    async def evaluate(self, script):
        return None


class FakeKeyboard:
    async def press(self, key):
        return None


class FakePage:
    """A Playwright page whose DOM is a selector -> element-specs mapping."""

    def __init__(self, url="https://www.google.com/maps/search/coffee", elements=None):
        self.url = url
        self.elements = elements or {}
        self.clicks = []
        self.keyboard = FakeKeyboard()
        self.goto_calls = []

    def locator(self, selector):
        return FakeLocator(selector, self.elements.get(selector, []), self)

    async def goto(self, url, **kwargs):
        self.goto_calls.append(url)

    async def wait_for_selector(self, selector, timeout=None):
        if not self.elements.get(selector):
            raise FakeTimeoutError(f"{selector} not found")
        return FakeLocator(selector, self.elements[selector], self)

    async def title(self):
        return "Google Maps"

    async def go_back(self):
        return None

    async def set_extra_http_headers(self, headers):
        return None


class FakeContext:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


# The place-details URL carries the business name, the CID and the
# coordinates, so `title` is recoverable even when every panel selector is
# dead. That is precisely why a title alone is not evidence of a good parse.
PLACE_URL = (
    "https://www.google.com/maps/place/Blue+Bottle+Coffee/"
    "@37.7749,-122.4194,15z/data=!4m5!3m4!1s0x808f7e2ab:0x1a2b3c4d!8m2"
)

FEED_SELECTOR = "div[role='feed']"
CARD_SELECTOR = "div[role='feed'] > div > div[jsaction]"
LINK_SELECTOR = "a[href*='/maps/place/']"


def _card(name):
    """One search-result card wrapping a place link."""
    return {
        "children": {
            LINK_SELECTOR: [
                {"attrs": {"aria-label": name, "href": "/maps/place/x"}},
            ]
        }
    }


def healthy_place_elements():
    """A details panel where the panel selectors still match."""
    return {
        "div[role='main']": [{}],
        "h1.DUwDvf": [{"text": "Blue Bottle Coffee"}],
        "div.F7nice span[aria-hidden='true']": [{"text": "4.5"}],
        "div.F7nice span[aria-label*='review']": [{"attrs": {"aria-label": "1,234 reviews"}}],
        "button[jsaction*='category']": [{"text": "Coffee shop"}],
        "button[data-item-id*='address']": [{"attrs": {"aria-label": "Address: 1 Market St, San Francisco"}}],
        "button[data-item-id*='phone']": [{"attrs": {"aria-label": "Phone: (415) 555-0100"}}],
        "a[data-item-id='authority']": [{"attrs": {"href": "https://bluebottlecoffee.com"}}],
    }


# Captured before any patching: `google_maps_scraper` does `import asyncio`, so
# patching its `asyncio.sleep` patches the shared module. The concurrency tests
# need a sleep that really yields to the event loop.
REAL_SLEEP = asyncio.sleep


@pytest.fixture(autouse=True)
def no_sleep():
    """Strip the scraper's fixed waits; they add ~8s per extraction."""

    async def instant(_seconds):
        return None

    with patch("asyncio.sleep", instant):
        yield


@pytest.fixture(autouse=True)
def reset_limits():
    """Restore the module's concurrency limits after every test."""
    browsers, fanout = get_max_concurrent_browsers(), get_max_fanout()
    yield
    configure_limits(max_concurrent_browsers=browsers, max_fanout=fanout)


def make_scraper(page):
    """A scraper whose page creation is stubbed out to ``page``."""
    scraper = GoogleMapsScraper(headless=True)
    context = FakeContext()

    async def _create_page(language="en"):
        return page, context

    scraper._create_page = _create_page
    scraper._fake_context = context
    return scraper


# --------------------------------------------------------------------------
# Redis fake, for durability
# --------------------------------------------------------------------------


class FakeRedis:
    """Just enough Redis for RecordStore, with data outliving any one store."""

    def __init__(self):
        self.data = {}

    async def set(self, key, value, ex=None):
        self.data[key] = value

    async def get(self, key):
        return self.data.get(key)

    async def delete(self, key):
        return 1 if self.data.pop(key, None) is not None else 0

    def scan_iter(self, match=None, count=None):
        async def gen():
            for key in list(self.data):
                if match is None or fnmatch.fnmatch(key, match):
                    yield key

        return gen()


@pytest.fixture
def memory_store():
    """A JobStore on the explicitly non-durable in-memory backend."""
    record_store = RecordStore("test:maps:jobs", ttl_seconds=60)
    with patch.object(RecordStore, "_get_redis", return_value=None):
        yield JobStore(record_store)


def job(job_id="job-1", owner="alice", **kwargs):
    kwargs.setdefault("name", "search_coffee")
    kwargs.setdefault("query", "coffee")
    return ScrapeJob(id=job_id, owner=owner, **kwargs)


# ==========================================================================
# 1. Owner scoping -- the security defect
# ==========================================================================


@pytest.mark.asyncio
class TestOwnerScoping:
    async def test_get_does_not_return_another_owners_job(self, memory_store):
        await memory_store.create(job("job-1", owner="alice"))

        assert await memory_store.get("alice", "job-1") is not None
        assert await memory_store.get("mallory", "job-1") is None

    async def test_delete_does_not_touch_another_owners_job(self, memory_store):
        await memory_store.create(job("job-1", owner="alice"))

        deleted_by_other = await memory_store.delete("mallory", "job-1")
        assert deleted_by_other is False
        # Alice's job is untouched, not merely un-reported.
        assert await memory_store.get("alice", "job-1") is not None
        deleted_by_owner = await memory_store.delete("alice", "job-1")
        assert deleted_by_owner is True

    async def test_list_only_returns_the_callers_own_jobs(self, memory_store):
        await memory_store.create(job("a1", owner="alice"))
        await memory_store.create(job("a2", owner="alice"))
        await memory_store.create(job("m1", owner="mallory"))

        alice_ids = {j.id for j in await memory_store.list_for_owner("alice")}
        mallory_ids = {j.id for j in await memory_store.list_for_owner("mallory")}

        assert alice_ids == {"a1", "a2"}
        assert mallory_ids == {"m1"}

    async def test_list_all_no_longer_exists(self):
        # The un-scoped enumeration method was the vulnerability; a caller that
        # still uses it must fail loudly rather than leak every tenant's jobs.
        assert not hasattr(JobStore, "list_all")

    async def test_status_filter_stays_owner_scoped(self, memory_store):
        await memory_store.create(job("a1", owner="alice", status=JobStatus.COMPLETED))
        await memory_store.create(job("a2", owner="alice", status=JobStatus.FAILED))
        await memory_store.create(job("m1", owner="mallory", status=JobStatus.COMPLETED))

        completed = await memory_store.list_for_owner("alice", status="completed")

        assert [j.id for j in completed] == ["a1"]

    async def test_job_without_owner_is_refused(self, memory_store):
        orphan = ScrapeJob(id="job-1", name="n", query="coffee")

        with pytest.raises(ValueError, match="owner is required"):
            await memory_store.create(orphan)
        with pytest.raises(ValueError, match="owner is required"):
            await memory_store.update(orphan)


# ==========================================================================
# 2. Durability -- jobs must survive a restart and reach sibling workers
# ==========================================================================


@pytest.mark.asyncio
class TestDurability:
    async def test_job_survives_a_restart_on_the_durable_backend(self):
        redis = FakeRedis()

        with patch.object(RecordStore, "_get_redis", return_value=redis):
            before = JobStore(RecordStore("maps:jobs", ttl_seconds=60))
            await before.create(job("job-1", owner="alice", results=[{"title": "Blue Bottle"}], total=1))

            # A new process: fresh RecordStore and fresh JobStore, same Redis.
            after = JobStore(RecordStore("maps:jobs", ttl_seconds=60))
            reloaded = await after.get("alice", "job-1")

        assert reloaded is not None
        assert reloaded.query == "coffee"
        assert reloaded.results == [{"title": "Blue Bottle"}]
        assert reloaded.total == 1

    async def test_restart_preserves_owner_scoping(self):
        redis = FakeRedis()

        with patch.object(RecordStore, "_get_redis", return_value=redis):
            await JobStore(RecordStore("maps:jobs")).create(job("job-1", owner="alice"))
            after = JobStore(RecordStore("maps:jobs"))

            assert await after.get("mallory", "job-1") is None

    async def test_memory_backend_reports_itself_non_durable(self, memory_store):
        assert await memory_store.is_durable() is False

    async def test_redis_backend_reports_itself_durable(self):
        with patch.object(RecordStore, "_get_redis", return_value=FakeRedis()):
            assert await JobStore(RecordStore("maps:jobs")).is_durable() is True

    async def test_round_trip_keeps_fields_to_dict_would_drop(self, memory_store):
        original = job(
            "job-1",
            owner="alice",
            results=[{"title": "A"}, {"title": "B"}],
            language="de",
            max_results=42,
            geo_coordinates="37.7,-122.4",
            error="boom",
            selectors_stale=True,
        )
        await memory_store.create(original)

        reloaded = await memory_store.get("alice", "job-1")

        # to_dict() is the lossy gosom-compatible shape; persisting it would
        # silently drop results and job parameters across a restart.
        assert "results" not in original.to_dict()
        assert reloaded.results == [{"title": "A"}, {"title": "B"}]
        assert reloaded.language == "de"
        assert reloaded.max_results == 42
        assert reloaded.geo_coordinates == "37.7,-122.4"
        assert reloaded.error == "boom"
        assert reloaded.selectors_stale is True
        assert reloaded.status is JobStatus.PENDING


# ==========================================================================
# 3. Concurrency bounds
# ==========================================================================


@pytest.mark.asyncio
class TestBrowserConcurrency:
    async def _measure_peak(self, cap, workers=8):
        configure_limits(max_concurrent_browsers=cap)
        live, peak = set(), [0]

        async def fake_launch(self, _async_playwright):
            # A real yield, so the workers genuinely interleave and the peak
            # measures the semaphore rather than the scheduler.
            await REAL_SLEEP(0.01)

        with patch.object(GoogleMapsScraper, "_launch", fake_launch):

            async def worker(index):
                scraper = GoogleMapsScraper()
                await scraper._init_browser()
                live.add(index)
                peak[0] = max(peak[0], len(live))
                await REAL_SLEEP(0.01)
                try:
                    await scraper.close()
                finally:
                    live.discard(index)

            await asyncio.gather(*(worker(i) for i in range(workers)))

        return peak[0]

    async def test_peak_concurrent_browsers_never_exceeds_the_cap(self):
        assert await self._measure_peak(cap=2, workers=8) <= 2

    async def test_the_semaphore_is_what_binds_not_serialisation(self):
        # Without the cap the same workload reaches 8 simultaneous browsers,
        # so the previous assertion is testing the semaphore, not accidental
        # sequencing of the fake launch.
        assert await self._measure_peak(cap=8, workers=8) == 8

    async def test_all_workers_still_complete_under_the_cap(self):
        configure_limits(max_concurrent_browsers=2)
        completed = []

        async def fake_launch(self, _async_playwright):
            return None

        with patch.object(GoogleMapsScraper, "_launch", fake_launch):

            async def worker(index):
                scraper = GoogleMapsScraper()
                await scraper._init_browser()
                await scraper.close()
                completed.append(index)

            await asyncio.gather(*(worker(i) for i in range(6)))

        assert sorted(completed) == [0, 1, 2, 3, 4, 5]

    async def test_a_failed_launch_does_not_leak_its_permit(self):
        configure_limits(max_concurrent_browsers=1)

        async def exploding_launch(self, _async_playwright):
            raise RuntimeError("chromium missing")

        with patch.object(GoogleMapsScraper, "_launch", exploding_launch):
            for _ in range(3):
                scraper = GoogleMapsScraper()
                with pytest.raises(RuntimeError, match="chromium missing"):
                    await scraper._init_browser()
                assert scraper._semaphore is None

        # A cap of 1 that leaked would deadlock here rather than proceed.
        async def fake_launch(self, _async_playwright):
            return None

        with patch.object(GoogleMapsScraper, "_launch", fake_launch):
            scraper = GoogleMapsScraper()
            await asyncio.wait_for(scraper._init_browser(), timeout=2)
            await scraper.close()

    async def test_concurrent_init_on_one_scraper_takes_a_single_permit(self):
        # Two coroutines sharing a scraper used to race the `is None` check and
        # each take a permit, of which only one was ever released.
        configure_limits(max_concurrent_browsers=2)
        launches = []

        async def fake_launch(self, _async_playwright):
            launches.append(1)
            await REAL_SLEEP(0.01)

        with patch.object(GoogleMapsScraper, "_launch", fake_launch):
            scraper = GoogleMapsScraper()
            # Bounded: four racing inits against a cap of two would otherwise
            # deadlock here rather than fail.
            await asyncio.wait_for(
                asyncio.gather(*(scraper._init_browser() for _ in range(4))),
                timeout=2,
            )
            await scraper.close()

            assert len(launches) == 1

            # Both permits must still be available: a leak would block one.
            a, b = GoogleMapsScraper(), GoogleMapsScraper()
            await asyncio.wait_for(a._init_browser(), timeout=2)
            await asyncio.wait_for(b._init_browser(), timeout=2)
            await a.close()
            await b.close()

    async def test_close_is_idempotent_and_returns_one_permit_only(self):
        configure_limits(max_concurrent_browsers=1)

        async def fake_launch(self, _async_playwright):
            return None

        with patch.object(GoogleMapsScraper, "_launch", fake_launch):
            scraper = GoogleMapsScraper()
            await scraper._init_browser()
            await scraper.close()
            await scraper.close()

            # A double release would raise the cap to 2 and let both in.
            a, b = GoogleMapsScraper(), GoogleMapsScraper()
            await a._init_browser()
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(b._init_browser(), timeout=0.05)
            await a.close()


class TestFanoutCap:
    def test_an_11x11_grid_is_truncated_to_the_hard_limit(self):
        configure_limits(max_fanout=25)
        grid = [(lat, lng) for lat in range(11) for lng in range(11)]

        assert len(grid) == 121
        assert len(cap_fanout(grid, kind="grid search")) == 25

    def test_a_grid_within_the_limit_is_untouched(self):
        configure_limits(max_fanout=25)
        grid = [(0, i) for i in range(9)]

        assert cap_fanout(grid) == grid

    def test_the_cap_is_configurable(self):
        configure_limits(max_fanout=4)

        assert len(cap_fanout(list(range(121)))) == 4
        assert get_max_fanout() == 4

    def test_a_nonsense_cap_is_rejected_rather_than_meaning_unlimited(self):
        with pytest.raises(ValueError):
            configure_limits(max_fanout=0)
        with pytest.raises(ValueError):
            configure_limits(max_concurrent_browsers=0)


# ==========================================================================
# 4. Stale selectors must not look like an empty result
# ==========================================================================


@pytest.mark.asyncio
class TestStaleSelectorDetection:
    async def test_dom_change_raises_instead_of_returning_empty_success(self):
        # The feed and its cards still match, but every details-panel selector
        # is dead -- the exact shape of a Google markup rotation.
        page = FakePage(
            elements={
                FEED_SELECTOR: [{}],
                CARD_SELECTOR: [_card("Blue Bottle Coffee"), _card("Sightglass")],
            }
        )
        scraper = make_scraper(page)

        with pytest.raises(SelectorsStaleError) as excinfo:
            await scraper.search("coffee", max_results=5)

        error = excinfo.value
        assert error.attempted == 2
        assert error.extracted == 0
        assert error.status_code == 503
        assert error.to_dict()["selectors_stale"] is True

    async def test_a_genuinely_empty_area_still_returns_an_empty_list(self):
        # The results container rendered and holds nothing we recognise. This
        # is what a genuinely empty area looks like, so tightening stale
        # detection must NOT turn it into a 503 -- it is labelled instead, see
        # test_a_feed_where_nothing_matches_flags_the_empty_as_unverified.
        page = FakePage(elements={FEED_SELECTOR: [{}]})
        scraper = make_scraper(page)

        assert await scraper.search("igloo repair in death valley") == []

    async def test_a_stale_card_selector_is_not_reported_as_an_empty_area(self):
        # The feed rendered and the page is full of place links, but the card
        # selector matches none of them -- a card-markup rotation. Counting
        # only cards would have returned a successful empty list here.
        page = FakePage(
            elements={
                FEED_SELECTOR: [{}],
                LINK_SELECTOR: [{"attrs": {"aria-label": "Blue Bottle"}}] * 12,
            }
        )
        scraper = make_scraper(page)

        with pytest.raises(SelectorsStaleError, match="card selector matched none"):
            await scraper.search("coffee")

    async def test_cards_whose_link_selector_is_dead_are_not_an_empty_area(self):
        # Cards render, but the anchor inside each one no longer matches, so no
        # place is ever even attempted. `attempted` stays 0; only counting
        # visible candidates catches this.
        page = FakePage(
            elements={
                FEED_SELECTOR: [{}],
                CARD_SELECTOR: [{"children": {}}, {"children": {}}, {"children": {}}],
            }
        )
        scraper = make_scraper(page)

        with pytest.raises(SelectorsStaleError) as excinfo:
            await scraper.search("coffee")

        assert excinfo.value.attempted == 0
        assert excinfo.value.extracted == 0

    async def test_a_feed_where_nothing_matches_flags_the_empty_as_unverified(self):
        # The one irreducibly ambiguous case: the feed rendered but neither
        # cards nor place links matched, so an empty area and a wholesale
        # markup rotation are indistinguishable. Raising would 503 every
        # legitimately empty search, so the empty result is returned but
        # labelled -- it must never be reported as a confirmed zero.
        page = FakePage(elements={FEED_SELECTOR: [{}]})
        scraper = make_scraper(page)

        assert await scraper.search("coffee") == []
        assert scraper._unverified_empty is True

    async def test_cards_that_all_throw_are_not_an_empty_area(self):
        # Every card blows up on interaction. The per-card `except: continue`
        # must not add up to a successful empty result.
        page = FakePage(
            elements={
                FEED_SELECTOR: [{}],
                CARD_SELECTOR: [{"children": {LINK_SELECTOR: [{"raises": True}]}} for _ in range(3)],
            }
        )
        scraper = make_scraper(page)

        with pytest.raises(SelectorsStaleError):
            await scraper.search("coffee")

    async def test_no_results_container_at_all_is_reported_not_swallowed(self):
        # Nothing matched: not the feed, not a place link, not a place panel.
        # Empty and broken are indistinguishable here, so it must not claim
        # success -- this also covers a block/CAPTCHA interstitial.
        page = FakePage(elements={})
        scraper = make_scraper(page)

        with pytest.raises(SelectorsStaleError, match="No results container"):
            await scraper.search("coffee")

    async def test_title_only_results_are_treated_as_stale_not_as_data(self):
        # `title` is parsed out of the URL, so it survives a total panel
        # failure. A page of title-only records is a broken parse wearing a
        # successful result's clothes.
        page = FakePage(
            url=PLACE_URL,
            elements={
                FEED_SELECTOR: [{}],
                CARD_SELECTOR: [_card("Blue Bottle Coffee")],
            },
        )
        page.elements["div[role='main']"] = [{}]
        scraper = make_scraper(page)

        with pytest.raises(SelectorsStaleError) as excinfo:
            await scraper.search("coffee")

        assert excinfo.value.extracted == 1
        assert set(excinfo.value.missing) == set(CORE_PLACE_FIELDS)

    async def test_a_healthy_page_yields_places_and_is_not_flagged(self):
        elements = healthy_place_elements()
        elements[FEED_SELECTOR] = [{}]
        elements[CARD_SELECTOR] = [_card("Blue Bottle Coffee")]
        page = FakePage(url=PLACE_URL, elements=elements)
        scraper = make_scraper(page)

        results = await scraper.search("coffee", max_results=1)

        assert len(results) == 1
        place = results[0]
        assert place["title"] == "Blue Bottle Coffee"
        assert place["address"] == "1 Market St, San Francisco"
        assert place["phone"] == "(415) 555-0100"
        assert place["review_rating"] == 4.5
        assert place["partial"] is False
        assert place["selectors_stale"] is False

    async def test_missing_required_field_raises_rather_than_returning_none(self):
        # No title anywhere: not in the URL, not in the panel.
        page = FakePage(url="https://www.google.com/maps", elements={})
        scraper = make_scraper(page)

        with pytest.raises(PlaceExtractionError) as excinfo:
            await scraper._extract_place_details(page)

        assert excinfo.value.missing == ["title"]

    async def test_an_optional_selector_failure_is_recorded_not_hidden(self):
        elements = healthy_place_elements()
        # This selector does not merely match nothing -- it throws.
        elements["button[jsaction*='heroHeaderImage'] img, div[jsaction*='photo'] img, img.Uf0tqf"] = [{"raises": True}]
        page = FakePage(url=PLACE_URL, elements=elements)
        scraper = make_scraper(page)

        place = await scraper._extract_place_details(page)

        # The place is still returned -- photos are optional -- but the miss is
        # visible instead of being swallowed by `except Exception: pass`.
        assert place["title"] == "Blue Bottle Coffee"
        assert "photos" in place["missing_fields"]
        assert place["partial"] is False

    async def test_search_closes_the_context_even_when_selectors_are_stale(self):
        page = FakePage(
            elements={
                FEED_SELECTOR: [{}],
                CARD_SELECTOR: [_card("Blue Bottle Coffee")],
            }
        )
        scraper = make_scraper(page)

        with pytest.raises(SelectorsStaleError):
            await scraper.search("coffee")

        assert scraper._fake_context.closed is True


# ==========================================================================
# 5. Hours parsing
# ==========================================================================


class TestParseHoursLabel:
    def setup_method(self):
        self.scraper = GoogleMapsScraper()

    def test_parses_a_full_weekly_aria_label(self):
        label = (
            "Monday, 7 AM to 6 PM; Tuesday, 7 AM to 6 PM; Wednesday, 7 AM to 6 PM; "
            "Thursday, 7 AM to 6 PM; Friday, 7 AM to 7 PM; Saturday, 8 AM to 7 PM; "
            "Sunday, 8 AM to 5 PM. Hide open hours for the week"
        )

        hours = self.scraper._parse_hours_label(label)

        assert hours["Monday"] == ["7 AM–6 PM"]
        assert hours["Friday"] == ["7 AM–7 PM"]
        assert set(hours) == {
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        }

    def test_parses_a_partial_week_with_a_closed_day(self):
        hours = self.scraper._parse_hours_label("Saturday, Closed; Sunday, 9 AM to 3 PM")

        assert hours == {"Saturday": ["Closed"], "Sunday": ["9 AM–3 PM"]}

    def test_returns_none_for_a_label_with_no_days(self):
        assert self.scraper._parse_hours_label("Open ⋅ Closes 6 PM") is None

    def test_returns_none_for_empty_input(self):
        assert self.scraper._parse_hours_label("") is None
        assert self.scraper._parse_hours_label(None) is None


@pytest.mark.asyncio
class TestExtractExpandedHours:
    async def test_reads_a_realistic_expanded_hours_table(self):
        rows = [
            {"text": "Monday7 AM–6 PM"},
            {"text": "Tuesday7 AM–6 PM"},
            {"text": "WednesdayClosed"},
            {"text": "ThursdayOpen 24 hours"},
        ]
        page = FakePage(elements={"table tr, div[role='listitem']": rows})

        hours = await GoogleMapsScraper()._extract_expanded_hours(page)

        assert hours["Monday"] == ["7 AM–6 PM"]
        assert hours["Wednesday"] == ["Closed"]
        assert hours["Thursday"] == ["Open 24 hours"]

    async def test_returns_none_when_the_table_is_absent(self):
        assert await GoogleMapsScraper()._extract_expanded_hours(FakePage()) is None

    async def test_returns_none_when_rows_carry_no_recognisable_times(self):
        page = FakePage(elements={"table tr, div[role='listitem']": [{"text": "Monday see website"}]})

        assert await GoogleMapsScraper()._extract_expanded_hours(page) is None


# ==========================================================================
# 6. Job outcomes -- a failed scrape must not complete empty
# ==========================================================================


@pytest.mark.asyncio
class TestRunScrapeJob:
    async def _run(self, store, target, search, unverified_empty=False):
        async def fake_search(self, **kwargs):
            self._unverified_empty = unverified_empty
            return await search()

        with (
            patch("app.services.google_maps_scraper.get_job_store", return_value=store),
            patch.object(GoogleMapsScraper, "search", fake_search),
            patch.object(GoogleMapsScraper, "close", lambda self: asyncio.sleep(0)),
        ):
            await run_scrape_job(target)

    async def test_a_scrape_failure_marks_the_job_failed_with_a_real_error(self, memory_store):
        target = job("job-1", owner="alice")
        await memory_store.create(target)

        async def boom():
            raise TimeoutError("navigation timed out after 60000ms")

        await self._run(memory_store, target, boom)

        stored = await memory_store.get("alice", "job-1")
        assert stored.status is JobStatus.FAILED
        # Not completed-with-zero-results, and the message is diagnosable.
        assert stored.results == []
        assert "navigation timed out" in stored.error
        assert "TimeoutError" in stored.error

    async def test_an_error_with_no_message_still_records_its_type(self, memory_store):
        target = job("job-1", owner="alice")
        await memory_store.create(target)

        async def boom():
            raise RuntimeError()

        await self._run(memory_store, target, boom)

        stored = await memory_store.get("alice", "job-1")
        assert stored.status is JobStatus.FAILED
        assert stored.error == "RuntimeError"

    async def test_stale_selectors_fail_the_job_and_set_the_flag(self, memory_store):
        target = job("job-1", owner="alice")
        await memory_store.create(target)

        async def stale():
            raise SelectorsStaleError("markup changed", attempted=5, extracted=0, missing=["title"])

        await self._run(memory_store, target, stale)

        stored = await memory_store.get("alice", "job-1")
        assert stored.status is JobStatus.FAILED
        assert stored.selectors_stale is True
        assert stored.results == []

    async def test_a_successful_job_persists_its_results(self, memory_store):
        target = job("job-1", owner="alice")
        await memory_store.create(target)

        async def ok():
            return [{"title": "Blue Bottle"}, {"title": "Sightglass"}]

        await self._run(memory_store, target, ok)

        stored = await memory_store.get("alice", "job-1")
        assert stored.status is JobStatus.COMPLETED
        assert stored.total == 2
        assert stored.progress == 2
        assert stored.selectors_stale is False
        assert [p["title"] for p in stored.results] == ["Blue Bottle", "Sightglass"]

    async def test_a_genuinely_empty_search_still_completes(self, memory_store):
        # search() already refuses to return [] for a broken page, so an empty
        # list here really is an empty area and must not be failed.
        target = job("job-1", owner="alice")
        await memory_store.create(target)

        async def empty():
            return []

        await self._run(memory_store, target, empty)

        stored = await memory_store.get("alice", "job-1")
        assert stored.status is JobStatus.COMPLETED
        assert stored.total == 0
        assert stored.empty_unverified is False
        assert stored.to_dict()["empty_unverified"] is False

    async def test_an_unverifiable_empty_result_is_labelled_on_the_job(self, memory_store):
        # Completed, but the caller is told the zero could not be confirmed --
        # so an empty result is never silently authoritative.
        target = job("job-1", owner="alice")
        await memory_store.create(target)

        async def empty():
            return []

        await self._run(memory_store, target, empty, unverified_empty=True)

        stored = await memory_store.get("alice", "job-1")
        assert stored.status is JobStatus.COMPLETED
        assert stored.empty_unverified is True
        assert stored.to_dict()["empty_unverified"] is True

    async def test_a_non_empty_result_is_never_labelled_unverified(self, memory_store):
        target = job("job-1", owner="alice")
        await memory_store.create(target)

        async def one():
            return [{"title": "Blue Bottle"}]

        await self._run(memory_store, target, one, unverified_empty=True)

        stored = await memory_store.get("alice", "job-1")
        assert stored.empty_unverified is False
