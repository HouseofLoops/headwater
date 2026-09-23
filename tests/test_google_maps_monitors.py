"""Tests for real Maps monitors, webhook delivery and place history.

Everything under test here replaced a fabrication:

- ``create_monitor`` reported ``status: "active"`` for a monitor it never
  stored, so a GET on the id it had just handed back 404'd.
- ``register_webhook`` returned a UUID and delivered nothing, ever.
- ``get_place_history`` returned ``{"history": []}`` unconditionally, which a
  client reads as "monitored, and nothing changed".

So the tests are written as claims about durable state and real HTTP, not about
response shape: a monitor is fetched back after creation, a webhook delivery is
asserted to have actually been signed and posted, and a scrape failure is
asserted *not* to become a history entry.

No network and no browser: the store runs on its in-memory backend, DNS
resolution inside the URL guard is stubbed to a fixed map, and httpx is
replaced by a recording double.
"""

import asyncio
import hashlib
import hmac
import json
import socket
from unittest.mock import patch

import pytest

from app.services import google_maps_monitors as monitors
from app.services.record_store import RecordStore

# --------------------------------------------------------------------------
# Offline doubles
# --------------------------------------------------------------------------

# Hostname -> address the stubbed resolver returns. This exercises the *real*
# url_guard routability logic while never touching the network, including the
# DNS-rebinding case where a public-looking name resolves to loopback.
_DNS_MAP = {
    "hooks.example.com": "93.184.216.34",
    "www.google.com": "142.250.72.238",
    "other.example.com": "93.184.216.35",
    "rebind.example.com": "127.0.0.1",
    "127.0.0.1": "127.0.0.1",
    "169.254.169.254": "169.254.169.254",
    "10.0.0.5": "10.0.0.5",
}


def _fake_getaddrinfo(host, port, *args, **kwargs):
    try:
        address = _DNS_MAP[host]
    except KeyError:
        raise socket.gaierror(f"unknown test host {host!r}")
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]


class FakeResponse:
    def __init__(self, status_code, headers=None):
        self.status_code = status_code
        self.headers = headers or {}


class FakeAsyncClient:
    """Stand-in for httpx.AsyncClient that records every POST.

    ``queue`` is a list of responses (or exceptions) handed out in order; the
    last one repeats once exhausted, so a test can say "always 500".
    """

    calls: list = []
    queue: list = []

    def __init__(self, *args, **kwargs):
        self.follow_redirects = kwargs.get("follow_redirects")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, content=None, headers=None, extensions=None):
        FakeAsyncClient.calls.append(
            {"url": url, "content": content, "headers": headers, "extensions": extensions or {}}
        )
        item = FakeAsyncClient.queue[min(len(FakeAsyncClient.calls) - 1, len(FakeAsyncClient.queue) - 1)]
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Force in-memory storage, stub DNS, and replace httpx and real sleeping."""
    import httpx

    import app.core.url_guard as url_guard
    import app.services.record_store as record_store

    record_store._stores.clear()
    FakeAsyncClient.calls = []
    FakeAsyncClient.queue = [FakeResponse(200)]

    monkeypatch.setattr(url_guard.socket, "getaddrinfo", _fake_getaddrinfo)
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    # No allow-list configured: the host-allow-list layer degrades to "the host
    # asked for" and the routability layer does the work. Tests that want the
    # configured mode set the variable themselves.
    monkeypatch.delenv(monitors.WEBHOOK_ALLOWED_HOSTS_SETTING, raising=False)

    with patch.object(RecordStore, "_get_redis", return_value=None):
        yield

    record_store._stores.clear()


@pytest.fixture
def slept():
    """A sleep double that records the backoff delays asked for."""
    delays: list[float] = []

    async def _sleep(seconds):
        delays.append(seconds)

    _sleep.delays = delays
    return _sleep


ALICE = "owner-alice"
BOB = "owner-bob"


def place(**overrides):
    """A scraped-place result of the shape lookup_place returns."""
    data = {
        "name": "Cafe Test",
        "address": "1 Test St",
        "phone": "+1 555 0100",
        "website": "https://cafe.example",
        "hours": {"mon": ["9-5"]},
        "rating": 4.5,
        "review_count": 100,
        "price_level": "$$",
        "category": "Cafe",
    }
    data.update(overrides)
    return {"place": data}


# --------------------------------------------------------------------------
# The headline bug: created monitors were not stored
# --------------------------------------------------------------------------


@pytest.mark.asyncio
class TestMonitorPersistence:
    async def test_created_monitor_is_retrievable(self):
        """The exact old bug: create said active, the subsequent get 404'd."""
        created = await monitors.create_monitor(owner=ALICE, place_id="p1")
        assert created["status"] == "active"

        fetched = await monitors.get_monitor(owner=ALICE, monitor_id=created["monitor_id"])
        assert fetched["monitor_id"] == created["monitor_id"]
        assert fetched["place_id"] == "p1"

    async def test_created_monitor_appears_in_listing(self):
        created = await monitors.create_monitor(owner=ALICE, place_id="p1")
        listing = await monitors.list_monitors(owner=ALICE)
        assert [m["monitor_id"] for m in listing["monitors"]] == [created["monitor_id"]]
        assert listing["total"] == 1

    async def test_status_filtered_total_counts_the_filtered_set(self):
        """A total that ignores the filter reports monitors the caller was told
        they do not have."""
        created = await monitors.create_monitor(owner=ALICE, place_id="p1")
        await monitors.create_monitor(owner=ALICE, place_id="p2")

        store = monitors._monitor_store()
        record = await store.get(ALICE, created["monitor_id"])
        await store.put(ALICE, created["monitor_id"], {**record.data, "status": "paused"})

        active = await monitors.list_monitors(owner=ALICE, status="active")
        assert len(active["monitors"]) == 1
        assert active["total"] == 1

    async def test_unknown_monitor_raises(self):
        with pytest.raises(monitors.MonitorNotFound):
            await monitors.get_monitor(owner=ALICE, monitor_id="does-not-exist")

    async def test_delete_removes_it(self):
        created = await monitors.create_monitor(owner=ALICE, place_id="p1")
        await monitors.delete_monitor(owner=ALICE, monitor_id=created["monitor_id"])
        with pytest.raises(monitors.MonitorNotFound):
            await monitors.get_monitor(owner=ALICE, monitor_id=created["monitor_id"])

    async def test_requires_place_id_or_url(self):
        with pytest.raises(ValueError):
            await monitors.create_monitor(owner=ALICE)

    async def test_monitor_url_must_be_a_google_maps_url(self):
        """The monitor URL is fed to page.goto, so it is an SSRF sink too."""
        with pytest.raises(monitors.InvalidWebhookTarget):
            await monitors.create_monitor(owner=ALICE, url="https://169.254.169.254/latest/meta-data/")


@pytest.mark.asyncio
class TestMonitorOwnerIsolation:
    async def test_owner_b_cannot_see_owner_a_monitor(self):
        created = await monitors.create_monitor(owner=ALICE, place_id="p1")
        with pytest.raises(monitors.MonitorNotFound):
            await monitors.get_monitor(owner=BOB, monitor_id=created["monitor_id"])

    async def test_owner_b_cannot_delete_owner_a_monitor(self):
        created = await monitors.create_monitor(owner=ALICE, place_id="p1")
        with pytest.raises(monitors.MonitorNotFound):
            await monitors.delete_monitor(owner=BOB, monitor_id=created["monitor_id"])
        # Alice's monitor survived the attempt.
        assert await monitors.get_monitor(owner=ALICE, monitor_id=created["monitor_id"])

    async def test_listing_never_crosses_owners(self):
        await monitors.create_monitor(owner=ALICE, place_id="p1")
        await monitors.create_monitor(owner=BOB, place_id="p2")
        alice_ids = {m["place_id"] for m in (await monitors.list_monitors(owner=ALICE))["monitors"]}
        bob_ids = {m["place_id"] for m in (await monitors.list_monitors(owner=BOB))["monitors"]}
        assert alice_ids == {"p1"}
        assert bob_ids == {"p2"}


# --------------------------------------------------------------------------
# Webhooks: SSRF, signing, retry, health
# --------------------------------------------------------------------------


@pytest.mark.asyncio
class TestWebhookRegistrationRejectsSSRF:
    @pytest.mark.parametrize(
        "target",
        [
            "http://127.0.0.1/",
            "http://169.254.169.254/",
            "https://127.0.0.1/hook",
            "https://169.254.169.254/latest/meta-data/",
            "https://10.0.0.5/hook",
            # A public-looking name that resolves to loopback: the layer a
            # host-only check misses entirely.
            "https://rebind.example.com/hook",
            "file:///etc/passwd",
            "https://user:pass@hooks.example.com/hook",
        ],
    )
    async def test_rejected(self, target):
        with pytest.raises(monitors.InvalidWebhookTarget):
            await monitors.register_webhook(owner=ALICE, url=target, events=["monitor.changed"])

    async def test_rejection_message_is_generic(self):
        """The detailed reason is an internal-topology oracle; it stays in logs."""
        with pytest.raises(monitors.InvalidWebhookTarget) as excinfo:
            await monitors.register_webhook(owner=ALICE, url="https://127.0.0.1/hook", events=["x"])
        assert "127.0.0.1" not in excinfo.value.public_message

    async def test_public_target_accepted(self):
        result = await monitors.register_webhook(
            owner=ALICE, url="https://hooks.example.com/hook", events=["monitor.changed"]
        )
        assert result["webhook_id"]
        assert result["url"] == "https://hooks.example.com/hook"

    async def test_events_are_required(self):
        with pytest.raises(ValueError):
            await monitors.register_webhook(owner=ALICE, url="https://hooks.example.com/h", events=[])

    async def test_allow_list_when_configured(self, monkeypatch):
        monkeypatch.setenv(monitors.WEBHOOK_ALLOWED_HOSTS_SETTING, "hooks.example.com")
        await monitors.register_webhook(owner=ALICE, url="https://hooks.example.com/h", events=["monitor.changed"])
        # A different, equally public host is now refused.
        with pytest.raises(monitors.InvalidWebhookTarget):
            await monitors.register_webhook(owner=ALICE, url="https://other.example.com/h", events=["monitor.changed"])


@pytest.mark.asyncio
class TestWebhookOwnerIsolation:
    async def test_owner_b_cannot_delete_owner_a_webhook(self):
        hook = await monitors.register_webhook(
            owner=ALICE, url="https://hooks.example.com/h", events=["monitor.changed"]
        )
        with pytest.raises(monitors.WebhookNotFound):
            await monitors.delete_webhook(owner=BOB, webhook_id=hook["webhook_id"])
        assert (await monitors.list_webhooks(owner=ALICE))["total"] == 1

    async def test_owner_b_listing_is_empty(self):
        await monitors.register_webhook(owner=ALICE, url="https://hooks.example.com/h", events=["monitor.changed"])
        assert (await monitors.list_webhooks(owner=BOB))["webhooks"] == []

    async def test_listing_never_returns_the_secret(self):
        await monitors.register_webhook(
            owner=ALICE, url="https://hooks.example.com/h", events=["monitor.changed"], secret="s3cret"
        )
        listed = (await monitors.list_webhooks(owner=ALICE))["webhooks"][0]
        assert "secret" not in listed


@pytest.mark.asyncio
class TestWebhookDelivery:
    async def _register(self, secret="s3cret"):
        return await monitors.register_webhook(
            owner=ALICE,
            url="https://hooks.example.com/hook",
            events=["monitor.changed"],
            secret=secret,
        )

    async def test_delivery_is_hmac_signed_over_the_exact_body(self, slept):
        hook = await self._register()
        result = await monitors.deliver_webhook(
            owner=ALICE,
            webhook_id=hook["webhook_id"],
            event="monitor.changed",
            payload={"hello": "world"},
            sleep=slept,
        )
        assert result["delivered"] is True
        assert len(FakeAsyncClient.calls) == 1

        call = FakeAsyncClient.calls[0]
        expected = "sha256=" + hmac.new(b"s3cret", call["content"], hashlib.sha256).hexdigest()
        assert call["headers"][monitors.SIGNATURE_HEADER] == expected
        assert json.loads(call["content"])["data"] == {"hello": "world"}
        assert call["headers"][monitors.EVENT_HEADER] == "monitor.changed"

    async def test_a_wrong_secret_does_not_verify(self, slept):
        hook = await self._register()
        await monitors.deliver_webhook(
            owner=ALICE,
            webhook_id=hook["webhook_id"],
            event="monitor.changed",
            payload={},
            sleep=slept,
        )
        call = FakeAsyncClient.calls[0]
        wrong = "sha256=" + hmac.new(b"not-the-secret", call["content"], hashlib.sha256).hexdigest()
        assert call["headers"][monitors.SIGNATURE_HEADER] != wrong

    async def test_retries_with_exponential_backoff_then_gives_up(self, slept):
        FakeAsyncClient.queue = [FakeResponse(500)]
        hook = await self._register()

        result = await monitors.deliver_webhook(
            owner=ALICE,
            webhook_id=hook["webhook_id"],
            event="monitor.changed",
            payload={},
            sleep=slept,
        )

        assert result["delivered"] is False
        assert result["attempts"] == monitors.MAX_DELIVERY_ATTEMPTS
        assert len(FakeAsyncClient.calls) == monitors.MAX_DELIVERY_ATTEMPTS
        # Backoff doubles, and there is one sleep fewer than attempts.
        assert slept.delays == [1.0, 2.0, 4.0]

    async def test_transient_failure_then_success_stops_retrying(self, slept):
        FakeAsyncClient.queue = [FakeResponse(503), FakeResponse(200)]
        hook = await self._register()
        result = await monitors.deliver_webhook(
            owner=ALICE,
            webhook_id=hook["webhook_id"],
            event="monitor.changed",
            payload={},
            sleep=slept,
        )
        assert result["delivered"] is True
        assert result["attempts"] == 2

    async def test_permanent_4xx_is_not_retried(self, slept):
        FakeAsyncClient.queue = [FakeResponse(404)]
        hook = await self._register()
        result = await monitors.deliver_webhook(
            owner=ALICE,
            webhook_id=hook["webhook_id"],
            event="monitor.changed",
            payload={},
            sleep=slept,
        )
        assert result["delivered"] is False
        assert result["attempts"] == 1
        assert slept.delays == []

    async def test_connection_errors_are_retried(self, slept):
        FakeAsyncClient.queue = [ConnectionError("refused")]
        hook = await self._register()
        result = await monitors.deliver_webhook(
            owner=ALICE,
            webhook_id=hook["webhook_id"],
            event="monitor.changed",
            payload={},
            sleep=slept,
        )
        assert result["delivered"] is False
        assert result["attempts"] == monitors.MAX_DELIVERY_ATTEMPTS
        assert "ConnectionError" in result["error"]

    async def test_redirect_to_a_blocked_host_is_not_followed(self, slept):
        """A 3xx is a fresh caller-influenced URL and must be re-validated."""
        FakeAsyncClient.queue = [
            FakeResponse(302, {"location": "https://169.254.169.254/latest/meta-data/"}),
        ]
        hook = await self._register()
        result = await monitors.deliver_webhook(
            owner=ALICE,
            webhook_id=hook["webhook_id"],
            event="monitor.changed",
            payload={},
            sleep=slept,
        )
        assert result["delivered"] is False
        # Exactly one POST: the redirect target was never requested.
        assert len(FakeAsyncClient.calls) == 1
        # The one request made went to the original, approved target.
        assert FakeAsyncClient.calls[0]["headers"]["Host"] == "hooks.example.com"

    async def test_request_is_pinned_to_the_validated_address(self, slept):
        """Closes the DNS-rebinding window between validation and connection.

        The guard resolves the name and approves an address; handing the *name*
        to httpx would let it resolve again and be answered with 127.0.0.1. So
        the connection goes to the approved address, with the hostname carried
        in Host and in the TLS SNI so the certificate is still verified against
        the real name.
        """
        hook = await self._register()
        await monitors.deliver_webhook(
            owner=ALICE,
            webhook_id=hook["webhook_id"],
            event="monitor.changed",
            payload={},
            sleep=slept,
        )
        call = FakeAsyncClient.calls[0]
        assert "93.184.216.34" in call["url"]
        assert "hooks.example.com" not in call["url"]
        assert call["headers"]["Host"] == "hooks.example.com"
        assert call["extensions"]["sni_hostname"] == "hooks.example.com"

    async def test_relative_redirect_resolves_against_the_hostname(self, slept):
        FakeAsyncClient.queue = [
            FakeResponse(307, {"location": "/v2/hook"}),
            FakeResponse(200),
        ]
        hook = await self._register()
        result = await monitors.deliver_webhook(
            owner=ALICE,
            webhook_id=hook["webhook_id"],
            event="monitor.changed",
            payload={},
            sleep=slept,
        )
        assert result["delivered"] is True
        assert FakeAsyncClient.calls[1]["headers"]["Host"] == "hooks.example.com"
        assert FakeAsyncClient.calls[1]["url"].endswith("/v2/hook")

    async def test_list_webhooks_reports_real_delivery_health(self, slept):
        FakeAsyncClient.queue = [FakeResponse(500)]
        hook = await self._register()
        await monitors.deliver_webhook(
            owner=ALICE,
            webhook_id=hook["webhook_id"],
            event="monitor.changed",
            payload={},
            sleep=slept,
        )
        listed = (await monitors.list_webhooks(owner=ALICE))["webhooks"][0]
        assert listed["delivery_count"] == 1
        assert listed["failure_count"] == 1
        assert listed["success_count"] == 0
        assert listed["consecutive_failures"] == 1
        assert listed["last_status"] == 500
        assert listed["last_error"]

    async def test_successful_delivery_clears_the_failure_streak(self, slept):
        FakeAsyncClient.queue = [FakeResponse(500), FakeResponse(200)]
        hook = await self._register()
        await monitors.deliver_webhook(
            owner=ALICE,
            webhook_id=hook["webhook_id"],
            event="monitor.changed",
            payload={},
            sleep=slept,
        )
        listed = (await monitors.list_webhooks(owner=ALICE))["webhooks"][0]
        assert listed["consecutive_failures"] == 0
        assert listed["success_count"] == 1


# --------------------------------------------------------------------------
# Checking, diffing, and firing
# --------------------------------------------------------------------------


@pytest.mark.asyncio
class TestMonitorChecks:
    async def test_first_check_records_a_baseline_and_fires_nothing(self):
        created = await monitors.create_monitor(owner=ALICE, place_id="p1")

        async def fetch(_monitor):
            return place()

        result = await monitors.check_monitor(owner=ALICE, monitor_id=created["monitor_id"], fetch_place=fetch)
        assert result["changed"] is False
        assert FakeAsyncClient.calls == []

        stored = await monitors.get_monitor(owner=ALICE, monitor_id=created["monitor_id"])
        assert len(stored["history"]) == 1
        assert stored["history"][0]["kind"] == "baseline"

    async def test_unchanged_place_adds_no_history_and_fires_nothing(self):
        created = await monitors.create_monitor(owner=ALICE, place_id="p1")

        async def fetch(_monitor):
            return place()

        await monitors.check_monitor(owner=ALICE, monitor_id=created["monitor_id"], fetch_place=fetch)
        result = await monitors.check_monitor(owner=ALICE, monitor_id=created["monitor_id"], fetch_place=fetch)

        assert result["changed"] is False
        assert FakeAsyncClient.calls == []
        stored = await monitors.get_monitor(owner=ALICE, monitor_id=created["monitor_id"])
        assert len(stored["history"]) == 1  # still just the baseline

    async def test_a_real_change_is_diffed_recorded_and_fires_a_webhook(self):
        await monitors.register_webhook(owner=ALICE, url="https://hooks.example.com/hook", events=["monitor.changed"])
        created = await monitors.create_monitor(owner=ALICE, place_id="p1")

        calls = {"n": 0}

        async def fetch(_monitor):
            calls["n"] += 1
            return place() if calls["n"] == 1 else place(phone="+1 555 9999")

        await monitors.check_monitor(owner=ALICE, monitor_id=created["monitor_id"], fetch_place=fetch)
        result = await monitors.check_monitor(owner=ALICE, monitor_id=created["monitor_id"], fetch_place=fetch)

        assert result["changed"] is True
        assert result["changes"] == {"phone": {"old": "+1 555 0100", "new": "+1 555 9999"}}
        assert len(FakeAsyncClient.calls) == 1

        body = json.loads(FakeAsyncClient.calls[0]["content"])
        assert body["event"] == "monitor.changed"
        assert body["data"]["changes"]["phone"]["new"] == "+1 555 9999"

        stored = await monitors.get_monitor(owner=ALICE, monitor_id=created["monitor_id"])
        assert len(stored["history"]) == 2
        assert stored["history"][-1]["kind"] == "change"

    async def test_only_tracked_fields_are_diffed(self):
        created = await monitors.create_monitor(owner=ALICE, place_id="p1", track_fields=["phone"])

        calls = {"n": 0}

        async def fetch(_monitor):
            calls["n"] += 1
            return place() if calls["n"] == 1 else place(rating=1.0)

        await monitors.check_monitor(owner=ALICE, monitor_id=created["monitor_id"], fetch_place=fetch)
        result = await monitors.check_monitor(owner=ALICE, monitor_id=created["monitor_id"], fetch_place=fetch)
        assert result["changed"] is False

    async def test_a_failed_scrape_is_not_recorded_as_no_change(self):
        """An outage must not be written into history as an observation."""
        created = await monitors.create_monitor(owner=ALICE, place_id="p1")

        async def fetch(_monitor):
            return {"error": True, "message": "browser crashed"}

        result = await monitors.check_monitor(owner=ALICE, monitor_id=created["monitor_id"], fetch_place=fetch)
        assert result["checked"] is False
        assert result["error"] == "browser crashed"

        stored = await monitors.get_monitor(owner=ALICE, monitor_id=created["monitor_id"])
        assert stored["history"] == []
        assert stored["last_error"] == "browser crashed"
        assert FakeAsyncClient.calls == []

    async def test_a_monitor_url_that_stopped_being_permitted_is_not_scraped(self):
        """Rebinding guard: the URL is re-validated before every scheduled scrape.

        Days pass between creation and a check, and an allowed hostname can be
        repointed at an internal address in that window -- the URL then goes to
        Playwright inside the container network.
        """
        created = await monitors.create_monitor(owner=ALICE, url="https://www.google.com/maps/place/x")

        looked_up = []

        class Spy:
            async def lookup_place(self, url=None):
                looked_up.append(url)
                return place()

            async def get_place_by_id(self, place_id):
                return place()

        with (
            patch("app.services.google_maps_service.google_maps_service", Spy()),
            patch(
                "app.core.url_guard.socket.getaddrinfo",
                lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))],
            ),
        ):
            result = await monitors.check_monitor(owner=ALICE, monitor_id=created["monitor_id"])

        assert result["checked"] is False
        assert looked_up == [], "the scrape ran despite the target being disallowed"

    async def test_a_raising_scrape_is_reported_not_swallowed(self):
        created = await monitors.create_monitor(owner=ALICE, place_id="p1")

        async def fetch(_monitor):
            raise RuntimeError("playwright exploded")

        result = await monitors.check_monitor(owner=ALICE, monitor_id=created["monitor_id"], fetch_place=fetch)
        assert result["checked"] is False
        assert "playwright exploded" in result["error"]

    async def test_a_field_that_vanishes_from_the_scrape_is_not_a_change(self):
        """A panel that failed to render must not become 'phone changed to null'."""
        await monitors.register_webhook(owner=ALICE, url="https://hooks.example.com/hook", events=["monitor.changed"])
        created = await monitors.create_monitor(owner=ALICE, place_id="p1")

        calls = {"n": 0}

        async def fetch(_monitor):
            calls["n"] += 1
            if calls["n"] == 1:
                return place()
            partial = place()
            del partial["place"]["phone"]
            return partial

        await monitors.check_monitor(owner=ALICE, monitor_id=created["monitor_id"], fetch_place=fetch)
        result = await monitors.check_monitor(owner=ALICE, monitor_id=created["monitor_id"], fetch_place=fetch)

        assert result["changed"] is False
        assert FakeAsyncClient.calls == []

    async def test_a_field_that_comes_back_unchanged_is_still_not_a_change(self):
        """The last observed value is carried forward, not re-baselined."""
        created = await monitors.create_monitor(owner=ALICE, place_id="p1")

        calls = {"n": 0}

        async def fetch(_monitor):
            calls["n"] += 1
            if calls["n"] == 2:
                partial = place()
                del partial["place"]["phone"]
                return partial
            return place()

        for _ in range(3):
            result = await monitors.check_monitor(owner=ALICE, monitor_id=created["monitor_id"], fetch_place=fetch)
        assert result["changed"] is False

    async def test_webhook_not_subscribed_to_the_event_is_not_fired(self):
        await monitors.register_webhook(owner=ALICE, url="https://hooks.example.com/hook", events=["job.completed"])
        created = await monitors.create_monitor(owner=ALICE, place_id="p1")

        calls = {"n": 0}

        async def fetch(_monitor):
            calls["n"] += 1
            return place() if calls["n"] == 1 else place(phone="+1 555 9999")

        await monitors.check_monitor(owner=ALICE, monitor_id=created["monitor_id"], fetch_place=fetch)
        await monitors.check_monitor(owner=ALICE, monitor_id=created["monitor_id"], fetch_place=fetch)
        assert FakeAsyncClient.calls == []

    async def test_another_owners_webhook_is_never_fired(self):
        await monitors.register_webhook(owner=BOB, url="https://hooks.example.com/bob", events=["monitor.changed"])
        created = await monitors.create_monitor(owner=ALICE, place_id="p1")

        calls = {"n": 0}

        async def fetch(_monitor):
            calls["n"] += 1
            return place() if calls["n"] == 1 else place(phone="+1 555 9999")

        await monitors.check_monitor(owner=ALICE, monitor_id=created["monitor_id"], fetch_place=fetch)
        await monitors.check_monitor(owner=ALICE, monitor_id=created["monitor_id"], fetch_place=fetch)
        assert FakeAsyncClient.calls == []


# --------------------------------------------------------------------------
# The scheduler
# --------------------------------------------------------------------------


@pytest.mark.asyncio
class TestScheduler:
    async def test_run_due_checks_skips_monitors_that_are_not_due(self):
        await monitors.create_monitor(owner=ALICE, place_id="p1", check_interval_hours=24)

        async def fetch(_monitor):
            return place()

        assert await monitors.run_due_checks(fetch_place=fetch) == []

    async def test_run_due_checks_runs_monitors_that_are_due(self):
        created = await monitors.create_monitor(owner=ALICE, place_id="p1", check_interval_hours=1)

        async def fetch(_monitor):
            return place()

        # Ask as if the interval had elapsed.
        results = await monitors.run_due_checks(now=monitors._now() + 7200, fetch_place=fetch)
        assert [r["monitor_id"] for r in results] == [created["monitor_id"]]

    async def test_scheduler_covers_every_owner(self):
        await monitors.create_monitor(owner=ALICE, place_id="p1", check_interval_hours=1)
        await monitors.create_monitor(owner=BOB, place_id="p2", check_interval_hours=1)

        seen = []

        async def fetch(monitor):
            seen.append(monitor["place_id"])
            return place()

        await monitors.run_due_checks(now=monitors._now() + 7200, fetch_place=fetch)
        assert sorted(seen) == ["p1", "p2"]

    async def test_a_failing_check_also_refreshes_the_owner_index(self):
        """A monitor that keeps failing is still alive and must stay scheduled."""
        created = await monitors.create_monitor(owner=ALICE, place_id="p1")
        await monitors._owner_index_store().delete(monitors._INDEX_OWNER, ALICE)

        async def fetch(_monitor):
            return {"error": True, "message": "scrape failed"}

        await monitors.check_monitor(owner=ALICE, monitor_id=created["monitor_id"], fetch_place=fetch)
        assert await monitors._known_owners() == [ALICE]

    async def test_a_check_refreshes_the_owner_index(self):
        """Index entries carry a TTL; without refresh a live monitor goes dark.

        Writing the monitor renews only the monitor's own TTL. If the index
        entry written at creation were left to expire, the scheduler would stop
        seeing the owner while their monitor was still very much alive.
        """
        created = await monitors.create_monitor(owner=ALICE, place_id="p1")
        # Simulate the index entry having aged out.
        await monitors._owner_index_store().delete(monitors._INDEX_OWNER, ALICE)
        assert await monitors._known_owners() == []

        async def fetch(_monitor):
            return place()

        await monitors.check_monitor(owner=ALICE, monitor_id=created["monitor_id"], fetch_place=fetch)
        assert await monitors._known_owners() == [ALICE]

    async def test_start_and_stop_are_symmetric(self):
        task = monitors.start_monitor_scheduler(interval_seconds=0.01)
        assert not task.done()
        await monitors.stop_monitor_scheduler()
        assert task.cancelled() or task.done()

    async def test_start_is_idempotent(self):
        try:
            first = monitors.start_monitor_scheduler(interval_seconds=0.01)
            second = monitors.start_monitor_scheduler(interval_seconds=0.01)
            assert first is second
        finally:
            await monitors.stop_monitor_scheduler()

    async def test_stop_without_start_is_safe(self):
        await monitors.stop_monitor_scheduler()

    async def test_running_scheduler_actually_fires_a_change(self):
        """End to end through the real loop: tick -> diff -> signed delivery."""
        await monitors.register_webhook(owner=ALICE, url="https://hooks.example.com/hook", events=["monitor.changed"])
        created = await monitors.create_monitor(owner=ALICE, place_id="p1", check_interval_hours=1)

        # Make it due immediately.
        store = monitors._monitor_store()
        record = await store.get(ALICE, created["monitor_id"])
        data = dict(record.data)
        data["next_check"] = 0
        data["last_snapshot"] = {f: None for f in data["track_fields"]}
        await store.put(ALICE, created["monitor_id"], data)

        async def fetch(_monitor):
            return place()

        try:
            monitors.start_monitor_scheduler(interval_seconds=0.01, fetch_place=fetch)
            for _ in range(200):
                if FakeAsyncClient.calls:
                    break
                await asyncio.sleep(0.01)
        finally:
            await monitors.stop_monitor_scheduler()

        assert FakeAsyncClient.calls, "the scheduler never delivered the change"
        assert monitors.SIGNATURE_HEADER in FakeAsyncClient.calls[0]["headers"]


# --------------------------------------------------------------------------
# Place history
# --------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPlaceHistory:
    async def test_unmonitored_place_says_so_rather_than_returning_empty(self):
        """The old bug: {"history": []} reads as 'monitored, nothing changed'."""
        result = await monitors.get_place_history(owner=ALICE, place_id="never-seen")
        assert result["monitored"] is False
        assert result["history"] == []
        assert "no observations are available" in result["message"].lower()

    async def test_monitored_place_returns_real_recorded_entries(self):
        created = await monitors.create_monitor(owner=ALICE, place_id="p1")

        calls = {"n": 0}

        async def fetch(_monitor):
            calls["n"] += 1
            return place() if calls["n"] == 1 else place(rating=3.0)

        await monitors.check_monitor(owner=ALICE, monitor_id=created["monitor_id"], fetch_place=fetch)
        await monitors.check_monitor(owner=ALICE, monitor_id=created["monitor_id"], fetch_place=fetch)

        result = await monitors.get_place_history(owner=ALICE, place_id="p1")
        assert result["monitored"] is True
        assert len(result["history"]) == 2
        assert result["history"][-1]["changes"]["rating"]["new"] == 3.0

    async def test_history_is_owner_scoped(self):
        created = await monitors.create_monitor(owner=ALICE, place_id="p1")

        async def fetch(_monitor):
            return place()

        await monitors.check_monitor(owner=ALICE, monitor_id=created["monitor_id"], fetch_place=fetch)

        # Bob monitors nothing, so for Bob this place is simply unknown.
        result = await monitors.get_place_history(owner=BOB, place_id="p1")
        assert result["monitored"] is False

    async def test_field_filter(self):
        created = await monitors.create_monitor(owner=ALICE, place_id="p1")

        calls = {"n": 0}

        async def fetch(_monitor):
            calls["n"] += 1
            return place() if calls["n"] == 1 else place(rating=3.0)

        await monitors.check_monitor(owner=ALICE, monitor_id=created["monitor_id"], fetch_place=fetch)
        await monitors.check_monitor(owner=ALICE, monitor_id=created["monitor_id"], fetch_place=fetch)

        assert len((await monitors.get_place_history(owner=ALICE, place_id="p1", field="rating"))["history"]) == 1
        assert (await monitors.get_place_history(owner=ALICE, place_id="p1", field="phone"))["history"] == []

    async def test_date_window_excludes_older_entries(self):
        created = await monitors.create_monitor(owner=ALICE, place_id="p1")

        async def fetch(_monitor):
            return place()

        await monitors.check_monitor(owner=ALICE, monitor_id=created["monitor_id"], fetch_place=fetch)

        far_future = await monitors.get_place_history(owner=ALICE, place_id="p1", start_date="2999-01-01")
        assert far_future["monitored"] is True
        assert far_future["history"] == []

    async def test_bad_date_is_rejected(self):
        await monitors.create_monitor(owner=ALICE, place_id="p1")
        with pytest.raises(ValueError):
            await monitors.get_place_history(owner=ALICE, place_id="p1", start_date="last tuesday")
