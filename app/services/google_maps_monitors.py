"""
Place monitors and outbound webhooks for the Google Maps module.

This module replaces the previous fabricated monitor/webhook endpoints. Those
returned ``status: "active"`` for a monitor that was never stored (a subsequent
GET on the returned id 404'd), and a webhook id that never delivered anything.
Everything here is backed by :mod:`app.services.record_store`, so a record that
is reported as created can afterwards be read back, and a webhook that is
reported as registered is actually delivered to.

Three concerns live here:

**Storage.** Monitors live in the ``maps:monitors`` namespace and webhooks in
``maps:webhooks``, both owner-scoped by :func:`owner_id_for_api_key`. Owner
scoping is enforced by the store's key layout rather than by filtering after a
read, so one caller cannot address another caller's monitor at all.

**The scheduler.** ``record_store`` deliberately exposes no "list every owner's
records" method -- that absence is the whole point of the module. The scheduler
nevertheless has to find work across owners, so this module maintains an
explicit owner index: a separate namespace whose single synthetic owner
(:data:`_INDEX_OWNER`) holds one record per real owner that has ever created a
monitor. Enumerating owners is therefore a deliberate, auditable act against a
dedicated index rather than a hole punched in the store's scoping.

**Delivery.** A webhook target is a caller-supplied outbound URL, i.e. exactly
the SSRF sink class :mod:`app.core.url_guard` exists for. It is validated at
registration *and* again before every delivery -- a name that resolved to a
public address at registration can be repointed at ``169.254.169.254`` later,
so a registration-time-only check is a rebinding window measured in days.
Redirects are never followed blindly: the ``Location`` of a 3xx is re-validated
under the same policy before the hop is taken.

Deliveries are signed with HMAC-SHA256 over the exact request body so a
receiver can verify the payload came from us, and retried with exponential
backoff up to a bounded attempt count. Every attempt's outcome is recorded on
the webhook record, which is what lets ``list_webhooks`` report real health
instead of an invented ``"active"``.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Iterable, Optional
from urllib.parse import urlsplit

from app.core.url_guard import UrlNotAllowed, validate_outbound_url
from app.services.record_store import get_record_store, owner_id_for_api_key

logger = logging.getLogger(__name__)

__all__ = [
    "MONITOR_NAMESPACE",
    "WEBHOOK_NAMESPACE",
    "DEFAULT_TRACK_FIELDS",
    "SIGNATURE_HEADER",
    "MonitorNotFound",
    "WebhookNotFound",
    "InvalidWebhookTarget",
    "create_monitor",
    "list_monitors",
    "get_monitor",
    "delete_monitor",
    "register_webhook",
    "list_webhooks",
    "delete_webhook",
    "get_place_history",
    "check_monitor",
    "run_due_checks",
    "deliver_webhook",
    "start_monitor_scheduler",
    "stop_monitor_scheduler",
    "owner_id_for_api_key",
]


MONITOR_NAMESPACE = "maps:monitors"
WEBHOOK_NAMESPACE = "maps:webhooks"
OWNER_INDEX_NAMESPACE = "maps:monitor-owners"

# Synthetic owner under which the owner index lives. It is not derived from any
# API key, so no caller can ever hold it -- see the module docstring.
_INDEX_OWNER = "__scheduler_index__"

# Fields worth watching on a place. A caller may name any subset (or any other
# scraped field); this is only the default.
DEFAULT_TRACK_FIELDS: tuple[str, ...] = (
    "name",
    "address",
    "phone",
    "website",
    "hours",
    "rating",
    "review_count",
    "price_level",
    "category",
)

# Bound on stored history so a long-lived monitor cannot grow without limit.
MAX_HISTORY_ENTRIES = 200

# Delivery tuning. Four attempts at 1s/2s/4s covers a receiver restart without
# holding a task open for minutes.
MAX_DELIVERY_ATTEMPTS = 4
INITIAL_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 30.0
DELIVERY_TIMEOUT_SECONDS = 10.0
MAX_REDIRECT_HOPS = 2

SIGNATURE_HEADER = "X-Headwater-Signature"
EVENT_HEADER = "X-Headwater-Event"
DELIVERY_HEADER = "X-Headwater-Delivery"
TIMESTAMP_HEADER = "X-Headwater-Timestamp"

MONITOR_CHANGED_EVENT = "monitor.changed"

# Name of the setting/environment variable holding the hosts webhooks may be
# delivered to. See :func:`_webhook_allowed_hosts` for what happens when it is
# unset -- the answer is deliberately documented rather than silently permissive.
WEBHOOK_ALLOWED_HOSTS_SETTING = "MAPS_WEBHOOK_ALLOWED_HOSTS"

# How often the scheduler wakes to look for due monitors.
DEFAULT_TICK_SECONDS = 60.0


class MonitorNotFound(LookupError):
    """No monitor with that id exists *for this owner*.

    Deliberately does not distinguish "does not exist" from "belongs to someone
    else": the distinction is an id-enumeration oracle.
    """


class WebhookNotFound(LookupError):
    """No webhook with that id exists for this owner."""


class InvalidWebhookTarget(ValueError):
    """A webhook URL failed outbound-URL validation.

    Attributes:
        reason: Detailed cause, safe to log; never return it to the caller.
        public_message: Generic message for the HTTP response body.
    """

    def __init__(self, reason: str, public_message: str = "The webhook URL is not permitted.") -> None:
        super().__init__(reason)
        self.reason = reason
        self.public_message = public_message


# ---------------------------------------------------------------------------
# Stores
# ---------------------------------------------------------------------------


def _monitor_store():
    return get_record_store(MONITOR_NAMESPACE)


def _webhook_store():
    return get_record_store(WEBHOOK_NAMESPACE)


def _owner_index_store():
    return get_record_store(OWNER_INDEX_NAMESPACE)


def _now() -> float:
    return time.time()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Webhook target validation
# ---------------------------------------------------------------------------


def _webhook_allowed_hosts() -> Optional[tuple[str, ...]]:
    """Return the configured webhook host allow-list, or None if unset.

    Read from ``Settings.MAPS_WEBHOOK_ALLOWED_HOSTS`` when that field exists,
    otherwise from the environment variable of the same name, as a comma
    separated list. Returning None means "not configured" and is handled by
    :func:`validate_webhook_target`.
    """
    raw: Any = None
    try:
        from app.core.config import get_settings

        raw = getattr(get_settings(), WEBHOOK_ALLOWED_HOSTS_SETTING, None)
    except Exception as exc:  # pragma: no cover - settings import is env dependent
        logger.debug("Could not read settings for webhook allow-list: %s", exc)

    if raw is None:
        raw = os.environ.get(WEBHOOK_ALLOWED_HOSTS_SETTING)

    if not raw:
        return None
    if isinstance(raw, str):
        entries = tuple(h.strip().lower() for h in raw.split(",") if h.strip())
    else:
        entries = tuple(str(h).strip().lower() for h in raw if str(h).strip())
    return entries or None


def validate_webhook_target(url: str, *, resolve_dns: bool = True):
    """Validate a webhook delivery target.

    Args:
        url: The caller-supplied webhook URL.
        resolve_dns: Perform DNS resolution and the routability check. Only set
            False in unit tests that must not touch the network.

    Returns:
        The :class:`~app.core.url_guard.ValidatedUrl`.

    Raises:
        InvalidWebhookTarget: if the URL is not a permitted delivery target.

    Note:
        When :data:`WEBHOOK_ALLOWED_HOSTS_SETTING` is configured, the host must
        match it. When it is *not* configured the host allow-list degenerates to
        "the host the caller asked for" -- the scheme, credential, port, DNS and
        global-routability layers of :func:`validate_outbound_url` still apply,
        which is what rejects ``127.0.0.1``, ``169.254.169.254``, ``10.0.0.0/8``
        and friends. That is the meaningful defence for a target that is by
        definition caller-chosen, but it is weaker than an allow-list: an
        operator who can enumerate their own public egress targets should set
        the setting. This is stated rather than hidden because a reader must be
        able to tell which of the two modes is in force.
    """
    if not isinstance(url, str) or not url.strip():
        raise InvalidWebhookTarget("webhook URL is empty")

    allowed = _webhook_allowed_hosts()
    if allowed is None:
        try:
            host = (urlsplit(url.strip()).hostname or "").lower()
        except ValueError as exc:
            raise InvalidWebhookTarget(f"webhook URL is unparseable: {exc}") from exc
        if not host:
            raise InvalidWebhookTarget("webhook URL has no host")
        allowed = (host,)

    try:
        return validate_outbound_url(url, allowed_hosts=allowed, resolve_dns=resolve_dns)
    except UrlNotAllowed as exc:
        raise InvalidWebhookTarget(exc.reason, exc.public_message) from exc


# ---------------------------------------------------------------------------
# Owner index
# ---------------------------------------------------------------------------


async def _remember_owner(owner: str) -> None:
    """Record that ``owner`` has at least one monitor, for the scheduler."""
    await _owner_index_store().put(_INDEX_OWNER, owner, {"owner": owner, "seen_at": _now()})


async def _known_owners(limit: int = 10_000) -> list[str]:
    """Every owner that has ever created a monitor."""
    records = await _owner_index_store().list_for_owner(_INDEX_OWNER, limit=limit)
    return [r.data.get("owner", r.id) for r in records]


async def _forget_owner_if_empty(owner: str) -> None:
    """Drop ``owner`` from the index once their last monitor is gone."""
    remaining = await _monitor_store().list_for_owner(owner, limit=1)
    if not remaining:
        await _owner_index_store().delete(_INDEX_OWNER, owner)


# ---------------------------------------------------------------------------
# Monitors
# ---------------------------------------------------------------------------


def _monitor_view(record, *, include_history: bool) -> dict[str, Any]:
    """Public shape of a stored monitor."""
    data = dict(record.data)
    history = data.pop("history", [])
    data.pop("last_snapshot", None)
    view = {
        "monitor_id": record.id,
        "created_at": _iso(record.created_at),
        "updated_at": _iso(record.updated_at),
        "history_entries": len(history),
        **data,
    }
    if include_history:
        view["history"] = history
    return view


async def create_monitor(
    *,
    owner: str,
    place_id: Optional[str] = None,
    url: Optional[str] = None,
    webhook_url: Optional[str] = None,
    check_interval_hours: int = 24,
    track_fields: Optional[Iterable[str]] = None,
) -> dict[str, Any]:
    """Create and persist a monitor.

    The monitor is written to the store and registered in the owner index
    *before* this returns, so the ``active`` status it reports is a fact about
    durable state rather than a claim: a GET on the returned id will find it.

    Args:
        owner: Owner id from :func:`owner_id_for_api_key`.
        place_id: Place to monitor. One of ``place_id`` or ``url`` is required.
        url: Google Maps URL to monitor.
        webhook_url: Optional target notified when a tracked field changes.
            Validated now, and again before every delivery.
        check_interval_hours: Hours between checks (minimum 1).
        track_fields: Fields to watch; defaults to :data:`DEFAULT_TRACK_FIELDS`.

    Returns:
        The stored monitor.

    Raises:
        ValueError: if neither ``place_id`` nor ``url`` was supplied.
        InvalidWebhookTarget: if ``webhook_url`` is not a permitted target.
    """
    if not place_id and not url:
        raise ValueError("place_id or url is required")

    if url:
        # The monitor URL is fed to Playwright's page.goto inside the container
        # network, so it is the same sink class as a webhook target.
        from app.core.url_guard import MAPS_ALLOWED_HOSTS

        try:
            validate_outbound_url(url, allowed_hosts=MAPS_ALLOWED_HOSTS)
        except UrlNotAllowed as exc:
            raise InvalidWebhookTarget(exc.reason, exc.public_message) from exc

    if webhook_url:
        validate_webhook_target(webhook_url)

    interval = max(1, int(check_interval_hours))
    fields = tuple(track_fields) if track_fields else DEFAULT_TRACK_FIELDS

    monitor_id = str(uuid.uuid4())
    now = _now()
    record = await _monitor_store().put(
        owner,
        monitor_id,
        {
            "place_id": place_id,
            "url": url,
            "webhook_url": webhook_url,
            "check_interval_hours": interval,
            "track_fields": list(fields),
            "status": "active",
            "next_check": now + interval * 3600,
            "last_checked": None,
            "last_error": None,
            "check_count": 0,
            "change_count": 0,
            "last_snapshot": None,
            "history": [],
        },
    )
    await _remember_owner(owner)

    view = _monitor_view(record, include_history=False)
    view["next_check"] = _iso(record.data["next_check"])
    return view


async def list_monitors(
    *,
    owner: str,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List this owner's monitors. Never returns another owner's records."""
    predicate = None
    if status:
        wanted = status.lower()
        predicate = lambda r: str(r.data.get("status", "")).lower() == wanted  # noqa: E731

    records = await _monitor_store().list_for_owner(
        owner, limit=limit, offset=offset, predicate=predicate
    )
    # The total must count what the filter selected, not everything. A total
    # that ignores the filter reports monitors the caller was told they do not
    # have.
    total = len(
        await _monitor_store().list_for_owner(owner, limit=10_000, predicate=predicate)
    )
    monitors = []
    for record in records:
        view = _monitor_view(record, include_history=False)
        if view.get("next_check"):
            view["next_check"] = _iso(view["next_check"])
        if view.get("last_checked"):
            view["last_checked"] = _iso(view["last_checked"])
        monitors.append(view)
    return {"monitors": monitors, "total": total}


async def get_monitor(*, owner: str, monitor_id: str, include_history: bool = True) -> dict[str, Any]:
    """Fetch one monitor.

    Raises:
        MonitorNotFound: if this owner has no such monitor.
    """
    record = await _monitor_store().get(owner, monitor_id)
    if record is None:
        raise MonitorNotFound(monitor_id)
    view = _monitor_view(record, include_history=include_history)
    if view.get("next_check"):
        view["next_check"] = _iso(view["next_check"])
    if view.get("last_checked"):
        view["last_checked"] = _iso(view["last_checked"])
    return view


async def delete_monitor(*, owner: str, monitor_id: str) -> None:
    """Delete one monitor.

    Raises:
        MonitorNotFound: if this owner has no such monitor.
    """
    if not await _monitor_store().delete(owner, monitor_id):
        raise MonitorNotFound(monitor_id)
    await _forget_owner_if_empty(owner)


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------


def _webhook_view(record) -> dict[str, Any]:
    """Public shape of a stored webhook. The secret is never included."""
    data = dict(record.data)
    data.pop("secret", None)
    return {
        "webhook_id": record.id,
        "created_at": _iso(record.created_at),
        "updated_at": _iso(record.updated_at),
        **data,
    }


async def register_webhook(
    *,
    owner: str,
    url: str,
    events: Iterable[str],
    secret: Optional[str] = None,
) -> dict[str, Any]:
    """Register a webhook and return it, including the signing secret.

    The secret is returned exactly once, here. It is stored but never echoed by
    :func:`list_webhooks`, because a listing endpoint that hands back signing
    secrets makes the signature worthless.

    Raises:
        InvalidWebhookTarget: if ``url`` is not a permitted delivery target.
        ValueError: if no events were requested.
    """
    event_list = [str(e) for e in events if str(e).strip()]
    if not event_list:
        raise ValueError("at least one event is required")

    validate_webhook_target(url)

    webhook_id = str(uuid.uuid4())
    signing_secret = secret or secrets.token_urlsafe(32)
    record = await _webhook_store().put(
        owner,
        webhook_id,
        {
            "url": url,
            "events": event_list,
            "secret": signing_secret,
            "status": "active",
            "delivery_count": 0,
            "success_count": 0,
            "failure_count": 0,
            "consecutive_failures": 0,
            "last_delivery_at": None,
            "last_status": None,
            "last_error": None,
        },
    )
    view = _webhook_view(record)
    view["secret"] = signing_secret
    return view


async def list_webhooks(*, owner: str, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """List this owner's webhooks with their real delivery health."""
    records = await _webhook_store().list_for_owner(owner, limit=limit, offset=offset)
    return {
        "webhooks": [_webhook_view(r) for r in records],
        "total": await _webhook_store().count_for_owner(owner),
    }


async def delete_webhook(*, owner: str, webhook_id: str) -> None:
    """Delete one webhook.

    Raises:
        WebhookNotFound: if this owner has no such webhook.
    """
    if not await _webhook_store().delete(owner, webhook_id):
        raise WebhookNotFound(webhook_id)


def sign_payload(secret: str, body: bytes) -> str:
    """Return the ``sha256=<hex>`` signature of ``body`` under ``secret``."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


async def deliver_webhook(
    *,
    owner: str,
    webhook_id: str,
    event: str,
    payload: dict[str, Any],
    max_attempts: int = MAX_DELIVERY_ATTEMPTS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict[str, Any]:
    """Deliver one event to one webhook, signed, with bounded retries.

    The target is re-validated on every attempt (not just at registration) and
    redirects are re-validated before being followed. Each attempt's outcome is
    written back to the webhook record so ``list_webhooks`` reports real health.

    Args:
        owner: Owner of the webhook.
        webhook_id: Which webhook to deliver to.
        event: Event name, e.g. ``monitor.changed``.
        payload: JSON-serialisable event body.
        max_attempts: Total attempts including the first (>= 1).
        sleep: Injection point for backoff, so tests need not wait in real time.

    Returns:
        ``{"delivered": bool, "attempts": int, "status_code": int | None,
        "error": str | None, "delivery_id": str}``

    Raises:
        WebhookNotFound: if this owner has no such webhook.
    """
    record = await _webhook_store().get(owner, webhook_id)
    if record is None:
        raise WebhookNotFound(webhook_id)

    delivery_id = str(uuid.uuid4())
    envelope = {
        "delivery_id": delivery_id,
        "event": event,
        "timestamp": _iso(_now()),
        "data": payload,
    }
    # Sign the exact bytes sent, not a re-serialisation of them: any difference
    # in separators or key order would make the receiver's check fail.
    body = json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = sign_payload(record.data.get("secret", ""), body)

    headers = {
        "Content-Type": "application/json",
        SIGNATURE_HEADER: signature,
        EVENT_HEADER: event,
        DELIVERY_HEADER: delivery_id,
        TIMESTAMP_HEADER: envelope["timestamp"],
        "User-Agent": "headwater-webhooks/1",
    }

    attempts = 0
    status_code: Optional[int] = None
    error: Optional[str] = None
    delivered = False

    while attempts < max(1, max_attempts):
        attempts += 1
        try:
            status_code = await _post_with_redirect_guard(record.data["url"], body, headers)
            if 200 <= status_code < 300:
                delivered = True
                error = None
                break
            error = f"receiver returned HTTP {status_code}"
            if 400 <= status_code < 500 and status_code not in (408, 429):
                # A 4xx that is not a timeout or a rate limit will not become a
                # 2xx on retry; retrying only burns attempts and hammers the
                # receiver.
                break
        except InvalidWebhookTarget as exc:
            # The target stopped being a legal destination. Never retry: the
            # retry would be the same SSRF attempt again.
            error = exc.public_message
            logger.warning("Webhook %s target rejected: %s", webhook_id, exc.reason)
            break
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"

        if attempts < max(1, max_attempts):
            backoff = min(INITIAL_BACKOFF_SECONDS * (2 ** (attempts - 1)), MAX_BACKOFF_SECONDS)
            await sleep(backoff)

    await _record_delivery_outcome(
        owner=owner,
        webhook_id=webhook_id,
        delivered=delivered,
        status_code=status_code,
        error=error,
    )

    return {
        "delivered": delivered,
        "attempts": attempts,
        "status_code": status_code,
        "error": error,
        "delivery_id": delivery_id,
    }


def _pinned_request(validated) -> tuple[str, dict[str, str], dict[str, str]]:
    """Rewrite a validated URL to connect to the address it resolved to.

    Validating a hostname and then handing the hostname to the HTTP client
    leaves a DNS-rebinding window: the client resolves the name a second time,
    and an attacker who controls the zone can answer ``127.0.0.1`` on that
    second lookup. Connecting to the address the guard already checked closes
    it. The original hostname is carried in the ``Host`` header and in httpx's
    ``sni_hostname`` extension, so TLS is still verified against the real
    hostname's certificate rather than against a bare IP.

    Returns:
        ``(url, host_header, extensions)`` for the pinned request.
    """
    import httpx

    if not validated.ip_addresses:
        return validated.url, {}, {}
    address = validated.ip_addresses[0]
    if ":" in address:  # IPv6 literals need brackets in a URL
        address = f"[{address}]"
    pinned = httpx.URL(validated.url).copy_with(host=address, port=validated.port)
    return str(pinned), {"Host": validated.host}, {"sni_hostname": validated.host}


async def _post_with_redirect_guard(url: str, body: bytes, headers: dict[str, str]) -> int:
    """POST ``body`` to ``url``, re-validating the target and any redirect.

    Returns:
        The HTTP status code of the final response.

    Raises:
        InvalidWebhookTarget: if the target, or a redirect it returns, is not a
            permitted destination.
    """
    import httpx

    target = url
    hops = 0
    async with httpx.AsyncClient(timeout=DELIVERY_TIMEOUT_SECONDS, follow_redirects=False) as client:
        while True:
            # Re-validated per hop: a redirect is a fresh caller-influenced URL,
            # and following one blindly re-opens the SSRF hole the allow-list
            # closed.
            validated = validate_webhook_target(target)
            pinned_url, host_header, extensions = _pinned_request(validated)
            response = await client.post(
                pinned_url,
                content=body,
                headers={**headers, **host_header},
                extensions=extensions,
            )
            if response.status_code not in (301, 302, 303, 307, 308):
                return response.status_code
            location = response.headers.get("location")
            if not location:
                return response.status_code
            hops += 1
            if hops > MAX_REDIRECT_HOPS:
                raise InvalidWebhookTarget(f"webhook redirect chain exceeded {MAX_REDIRECT_HOPS} hops")
            # Join against the *logical* URL, not the pinned one: a relative
            # Location must resolve against the hostname, not against an IP.
            target = str(httpx.URL(validated.url).join(location))


async def _record_delivery_outcome(
    *,
    owner: str,
    webhook_id: str,
    delivered: bool,
    status_code: Optional[int],
    error: Optional[str],
) -> None:
    """Write a delivery result back onto the webhook record."""
    record = await _webhook_store().get(owner, webhook_id)
    if record is None:
        return
    data = dict(record.data)
    data["delivery_count"] = int(data.get("delivery_count", 0)) + 1
    data["last_delivery_at"] = _now()
    data["last_status"] = status_code
    data["last_error"] = error
    if delivered:
        data["success_count"] = int(data.get("success_count", 0)) + 1
        data["consecutive_failures"] = 0
        data["status"] = "active"
    else:
        data["failure_count"] = int(data.get("failure_count", 0)) + 1
        data["consecutive_failures"] = int(data.get("consecutive_failures", 0)) + 1
        if data["consecutive_failures"] >= 10:
            data["status"] = "failing"
    await _webhook_store().put(owner, webhook_id, data)


async def _fire_monitor_webhooks(
    *,
    owner: str,
    monitor: dict[str, Any],
    monitor_id: str,
    changes: dict[str, Any],
    snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    """Deliver a ``monitor.changed`` event to every subscriber of this change.

    Two kinds of subscriber: the ad-hoc ``webhook_url`` given when the monitor
    was created, and any registered webhook whose ``events`` include
    ``monitor.changed``.
    """
    payload = {
        "monitor_id": monitor_id,
        "place_id": monitor.get("place_id"),
        "url": monitor.get("url"),
        "changes": changes,
        "snapshot": snapshot,
        "detected_at": _iso(_now()),
    }

    results: list[dict[str, Any]] = []

    registered = await _webhook_store().list_for_owner(
        owner,
        limit=100,
        predicate=lambda r: MONITOR_CHANGED_EVENT in (r.data.get("events") or []),
    )
    for record in registered:
        try:
            results.append(
                await deliver_webhook(
                    owner=owner,
                    webhook_id=record.id,
                    event=MONITOR_CHANGED_EVENT,
                    payload=payload,
                )
            )
        except WebhookNotFound:
            continue

    inline_url = monitor.get("webhook_url")
    if inline_url:
        results.append(await _deliver_inline(inline_url, MONITOR_CHANGED_EVENT, payload))

    return results


async def _deliver_inline(
    url: str,
    event: str,
    payload: dict[str, Any],
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict[str, Any]:
    """Deliver to a monitor's inline ``webhook_url``.

    This target has no registered secret, so the delivery is unsigned and says
    so in the result rather than pretending otherwise. A caller who wants
    signed deliveries registers a webhook.
    """
    delivery_id = str(uuid.uuid4())
    envelope = {
        "delivery_id": delivery_id,
        "event": event,
        "timestamp": _iso(_now()),
        "data": payload,
    }
    body = json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        EVENT_HEADER: event,
        DELIVERY_HEADER: delivery_id,
        "User-Agent": "headwater-webhooks/1",
    }

    attempts = 0
    status_code: Optional[int] = None
    error: Optional[str] = None
    delivered = False
    while attempts < MAX_DELIVERY_ATTEMPTS:
        attempts += 1
        try:
            status_code = await _post_with_redirect_guard(url, body, headers)
            if 200 <= status_code < 300:
                delivered = True
                error = None
                break
            error = f"receiver returned HTTP {status_code}"
            if 400 <= status_code < 500 and status_code not in (408, 429):
                break
        except InvalidWebhookTarget as exc:
            error = exc.public_message
            break
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        if attempts < MAX_DELIVERY_ATTEMPTS:
            await sleep(min(INITIAL_BACKOFF_SECONDS * (2 ** (attempts - 1)), MAX_BACKOFF_SECONDS))

    return {
        "delivered": delivered,
        "attempts": attempts,
        "status_code": status_code,
        "error": error,
        "delivery_id": delivery_id,
        "signed": False,
    }


# ---------------------------------------------------------------------------
# Checking and diffing
# ---------------------------------------------------------------------------


async def _default_fetch_place(monitor: dict[str, Any]) -> dict[str, Any]:
    """Re-scrape the monitored place. Imported lazily to avoid a cycle."""
    from app.core.url_guard import MAPS_ALLOWED_HOSTS
    from app.services.google_maps_service import google_maps_service

    if monitor.get("url"):
        # Re-validated on every check, not just at creation. Days can pass
        # between the two, and an allowed hostname can be repointed at an
        # internal address in that window -- the URL then goes to Playwright
        # inside the container network.
        try:
            validate_outbound_url(monitor["url"], allowed_hosts=MAPS_ALLOWED_HOSTS)
        except UrlNotAllowed as exc:
            logger.warning("Monitor URL no longer permitted: %s", exc.reason)
            return {"error": True, "message": "The monitored URL is no longer a permitted target."}
        return await google_maps_service.lookup_place(url=monitor["url"])
    return await google_maps_service.get_place_by_id(monitor["place_id"])


def _snapshot(place: dict[str, Any], track_fields: Iterable[str]) -> dict[str, Any]:
    """Project the scraped place down to the tracked fields we actually saw.

    A field the scrape did not return is *omitted*, not recorded as ``None``.
    Storing it as None would make the next diff report "phone changed to null"
    the first time a panel failed to render, which is an invented observation
    of exactly the kind this module exists to remove.
    """
    return {field: place[field] for field in track_fields if place.get(field) is not None}


def _diff(previous: Optional[dict[str, Any]], current: dict[str, Any]) -> dict[str, Any]:
    """Return ``{field: {"old": ..., "new": ...}}`` for fields that changed.

    Only fields observed in *both* snapshots are compared. A first observation
    (``previous is None``) is not a change -- reporting one would fire a webhook
    the moment a monitor is created, for a change nobody made -- and a field
    that has simply gone unobserved is not a change either, because we did not
    see it disappear, we just did not see it.
    """
    if previous is None:
        return {}
    changes: dict[str, Any] = {}
    for field, new_value in current.items():
        if field not in previous:
            continue
        old_value = previous[field]
        if old_value != new_value:
            changes[field] = {"old": old_value, "new": new_value}
    return changes


async def check_monitor(
    *,
    owner: str,
    monitor_id: str,
    fetch_place: Optional[Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]] = None,
) -> dict[str, Any]:
    """Run one check: re-scrape, diff, persist, and fire webhooks on change.

    Args:
        owner: Owner of the monitor.
        monitor_id: Which monitor to check.
        fetch_place: Injection point for the scrape, used by tests. Defaults to
            re-scraping through :mod:`app.services.google_maps_service`.

    Returns:
        ``{"monitor_id", "checked": bool, "changed": bool, "changes": {...},
        "error": str | None, "deliveries": [...]}``

    Raises:
        MonitorNotFound: if this owner has no such monitor.
    """
    record = await _monitor_store().get(owner, monitor_id)
    if record is None:
        raise MonitorNotFound(monitor_id)

    data = dict(record.data)
    fetch = fetch_place or _default_fetch_place
    now = _now()
    interval_seconds = int(data.get("check_interval_hours", 24)) * 3600

    try:
        result = await fetch(data)
    except Exception as exc:
        logger.error("Monitor %s scrape raised: %s", monitor_id, exc)
        result = {"error": True, "message": str(exc)}

    if result.get("error") or not result.get("place"):
        # A failed scrape is recorded as a failed scrape. It must not be
        # written into history as "no changes" -- that would turn an outage
        # into a fabricated observation that the place stayed the same.
        data["last_checked"] = now
        data["next_check"] = now + interval_seconds
        data["check_count"] = int(data.get("check_count", 0)) + 1
        data["last_error"] = result.get("message") or "place lookup failed"
        await _monitor_store().put(owner, monitor_id, data)
        # Refreshed here too: a monitor that keeps failing is still alive, and
        # letting its index entry lapse would quietly retire it from the
        # scheduler at exactly the moment someone needs to see the failures.
        await _remember_owner(owner)
        return {
            "monitor_id": monitor_id,
            "checked": False,
            "changed": False,
            "changes": {},
            "error": data["last_error"],
            "deliveries": [],
        }

    place = result["place"]
    track_fields = data.get("track_fields") or list(DEFAULT_TRACK_FIELDS)
    previous = data.get("last_snapshot")
    snapshot = _snapshot(place, track_fields)
    changes = _diff(previous, snapshot)

    history = list(data.get("history") or [])
    if previous is None or changes:
        history.append(
            {
                "timestamp": _iso(now),
                "changes": changes,
                "snapshot": snapshot,
                "kind": "baseline" if previous is None else "change",
            }
        )
        history = history[-MAX_HISTORY_ENTRIES:]

    data["history"] = history
    # Merged, not replaced: a field this scrape did not return keeps its last
    # observed value, so the next scrape that does see it compares against
    # something real instead of silently re-baselining.
    data["last_snapshot"] = {**(previous or {}), **snapshot}
    data["last_checked"] = now
    data["next_check"] = now + interval_seconds
    data["check_count"] = int(data.get("check_count", 0)) + 1
    data["last_error"] = None
    if changes:
        data["change_count"] = int(data.get("change_count", 0)) + 1
    await _monitor_store().put(owner, monitor_id, data)
    # Refresh the owner index alongside the monitor. Both carry the store's TTL;
    # writing the monitor renews only its own, so an index entry written once at
    # creation would expire under a monitor that is still alive, and the
    # scheduler would stop seeing that owner entirely.
    await _remember_owner(owner)

    deliveries: list[dict[str, Any]] = []
    if changes:
        deliveries = await _fire_monitor_webhooks(
            owner=owner,
            monitor=data,
            monitor_id=monitor_id,
            changes=changes,
            snapshot=snapshot,
        )

    return {
        "monitor_id": monitor_id,
        "checked": True,
        "changed": bool(changes),
        "changes": changes,
        "error": None,
        "deliveries": deliveries,
    }


async def run_due_checks(
    *,
    now: Optional[float] = None,
    fetch_place: Optional[Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]] = None,
) -> list[dict[str, Any]]:
    """Check every active monitor whose ``next_check`` has passed.

    Walks the owner index rather than the monitor store directly, because the
    store has no cross-owner listing by design.
    """
    cutoff = now if now is not None else _now()
    results: list[dict[str, Any]] = []

    for owner in await _known_owners():
        try:
            due = await _monitor_store().list_for_owner(
                owner,
                limit=500,
                predicate=lambda r: (
                    str(r.data.get("status", "active")).lower() == "active"
                    and float(r.data.get("next_check") or 0) <= cutoff
                ),
            )
        except Exception as exc:
            logger.error("Listing monitors for owner failed: %s", exc)
            continue

        for record in due:
            try:
                results.append(await check_monitor(owner=owner, monitor_id=record.id, fetch_place=fetch_place))
            except MonitorNotFound:
                continue
            except Exception as exc:
                # One monitor blowing up must not stop the tick, but it is
                # logged loudly rather than swallowed.
                logger.exception("Monitor %s check failed: %s", record.id, exc)

    return results


# ---------------------------------------------------------------------------
# Place history
# ---------------------------------------------------------------------------


def _parse_boundary(value: Optional[str], *, end: bool) -> Optional[float]:
    """Parse a caller-supplied ISO date/datetime into a UTC timestamp."""
    if not value:
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid date {value!r}: expected ISO-8601") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if end and len(text) == 10:
        # A bare "2026-01-31" as an end bound means the whole of that day.
        parsed = parsed + timedelta(days=1) - timedelta(microseconds=1)
    return parsed.timestamp()


async def get_place_history(
    *,
    owner: str,
    place_id: str,
    field: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict[str, Any]:
    """Return the recorded history for a place, from this owner's monitors.

    History exists only where a monitor has been observing the place, so the
    result distinguishes three states explicitly rather than collapsing them
    into an empty list:

    - ``monitored: False`` -- no monitor of this owner has ever covered this
      place, so nothing is known about it. This is *not* "no changes".
    - ``monitored: True`` with an empty ``history`` -- monitored, but no
      observation falls inside the requested window.
    - ``monitored: True`` with entries -- the real recorded observations.

    Args:
        owner: Owner id; only this owner's monitors are consulted.
        place_id: Place whose history is wanted.
        field: Restrict to entries where this field changed.
        start_date: ISO date/datetime lower bound (inclusive).
        end_date: ISO date/datetime upper bound (inclusive).

    Raises:
        ValueError: if a date bound is not ISO-8601.
    """
    start_ts = _parse_boundary(start_date, end=False)
    end_ts = _parse_boundary(end_date, end=True)

    monitors = await _monitor_store().list_for_owner(
        owner, limit=500, predicate=lambda r: r.data.get("place_id") == place_id
    )

    if not monitors:
        return {
            "place_id": place_id,
            "monitored": False,
            "history": [],
            "monitor_ids": [],
            "message": (
                "No monitor of this caller currently holds history for this place, so "
                "no observations are available. Note this says nothing about the place: "
                "it means nothing has been watching it (or a monitor that was watching "
                "it has since been deleted or expired). Create a monitor to start "
                "recording changes."
            ),
        }

    entries: list[dict[str, Any]] = []
    for record in monitors:
        for entry in record.data.get("history") or []:
            try:
                ts = datetime.fromisoformat(entry["timestamp"]).timestamp()
            except (KeyError, ValueError):
                continue
            if start_ts is not None and ts < start_ts:
                continue
            if end_ts is not None and ts > end_ts:
                continue
            if field and field not in (entry.get("changes") or {}):
                continue
            entries.append({**entry, "monitor_id": record.id})

    entries.sort(key=lambda e: e["timestamp"])

    return {
        "place_id": place_id,
        "monitored": True,
        "monitor_ids": [r.id for r in monitors],
        "history": entries,
        "total": len(entries),
    }


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

_scheduler_task: Optional[asyncio.Task] = None


async def _scheduler_loop(
    interval_seconds: float,
    fetch_place: Optional[Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]],
) -> None:
    """Wake every ``interval_seconds`` and run any due checks."""
    logger.info("Maps monitor scheduler started (tick=%ss)", interval_seconds)
    try:
        while True:
            try:
                results = await run_due_checks(fetch_place=fetch_place)
                if results:
                    changed = sum(1 for r in results if r.get("changed"))
                    logger.info("Monitor tick: %d checked, %d changed", len(results), changed)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # The loop must survive a bad tick; a scheduler that dies on the
                # first transient error silently stops all monitoring.
                logger.exception("Monitor tick failed: %s", exc)
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        logger.info("Maps monitor scheduler stopped")
        raise


def start_monitor_scheduler(
    *,
    interval_seconds: float = DEFAULT_TICK_SECONDS,
    fetch_place: Optional[Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]] = None,
) -> asyncio.Task:
    """Start the background monitor scheduler.

    Call from the application lifespan startup. Idempotent: calling it while a
    scheduler is already running returns the existing task rather than starting
    a second one that would double-fire every webhook.

    Args:
        interval_seconds: Seconds between ticks.
        fetch_place: Injection point for the scrape, used by tests.

    Returns:
        The scheduler task.
    """
    global _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        return _scheduler_task
    _scheduler_task = asyncio.create_task(
        _scheduler_loop(interval_seconds, fetch_place), name="maps-monitor-scheduler"
    )
    return _scheduler_task


async def stop_monitor_scheduler() -> None:
    """Cancel the background scheduler and wait for it to finish.

    Call from the application lifespan shutdown. Safe to call when no scheduler
    is running.
    """
    global _scheduler_task
    task = _scheduler_task
    _scheduler_task = None
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
