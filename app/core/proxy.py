# app/core/proxy.py
"""
Outbound proxy selection.

This module previously read its configuration with ``os.getenv`` at import
time:

    PROXY_URLS = os.getenv('PROXY_URLS', '')
    ENABLE_PROXY = os.getenv('ENABLE_PROXY', 'false').lower() == 'true'

pydantic-settings loads ``.env`` into the ``Settings`` object; it does **not**
export those values into ``os.environ``. So for a bare ``uvicorn main:app``
run -- the documented local workflow -- both names were always empty and
proxying was silently off. It appeared to work only under Docker and
docker-compose, which put the variables into the real environment.

That is the same defect as the API-key bug (auth read keys via ``os.getenv``
and never saw them), and it fails the same way: quietly, with every request
going out un-proxied while the configuration says otherwise.

Configuration is now read from ``Settings`` and re-read on each call, so a
settings reload takes effect. ``Settings`` declares both ``PROXY_URLS``
(comma-separated, what this module has always parsed) and a legacy singular
``PROXY_URL``; both are accepted and merged, since the README documented the
singular form for a long time.
"""

import asyncio
import itertools
import logging
import re

logger = logging.getLogger("uvicorn")

__all__ = [
    "get_available_proxies",
    "get_proxy",
    "get_proxy_sync",
    "is_host_excluded",
    "is_proxy_enabled",
    "mask_proxy",
    "proxy_for",
    "rotate_proxy",
]

_proxy_lock = asyncio.Lock()

# Round-robin cursor, rebuilt whenever the resolved proxy list changes so that
# a settings reload does not keep cycling a stale list.
_proxy_iter: itertools.cycle | None = None
_proxy_iter_source: tuple[str, ...] = ()


def is_valid_url(url: str) -> bool:
    return re.match(r"^(http|https):\/\/[^\s\/$.?#].[^\s]*$", url) is not None


def _load_proxy_config() -> tuple[bool, list[str]]:
    """Read proxy configuration from Settings.

    Returns:
        ``(enabled, proxy_urls)``. On any settings failure this returns
        ``(False, [])`` -- proxying off -- rather than raising, so a
        configuration problem degrades to direct connections instead of
        breaking every request path that imports this module.
    """
    try:
        from app.core.config import get_settings

        settings = get_settings()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not load settings for proxy configuration: %s", exc)
        return False, []

    enabled = bool(getattr(settings, "ENABLE_PROXY", False))

    raw_values: list[str] = []
    for attr in ("PROXY_URLS", "PROXY_URL"):
        value = getattr(settings, attr, None)
        if not value:
            continue
        if isinstance(value, (list, tuple)):
            raw_values.extend(str(v) for v in value)
        else:
            raw_values.extend(str(value).split(","))

    urls: list[str] = []
    for candidate in raw_values:
        candidate = candidate.strip()
        if not candidate:
            continue
        if not is_valid_url(candidate):
            logger.warning("Ignoring malformed proxy URL: %r", candidate)
            continue
        if candidate not in urls:  # preserve order, drop duplicates
            urls.append(candidate)

    return enabled, urls


def _next_proxy(urls: list[str]) -> str | None:
    """Advance the round-robin cursor, rebuilding it if the list changed."""
    global _proxy_iter, _proxy_iter_source

    key = tuple(urls)
    if not key:
        _proxy_iter, _proxy_iter_source = None, ()
        return None

    if _proxy_iter is None or _proxy_iter_source != key:
        _proxy_iter = itertools.cycle(urls)
        _proxy_iter_source = key

    return next(_proxy_iter)


def is_proxy_enabled() -> bool:
    """True when proxying is enabled AND at least one valid proxy is configured.

    Prefer this over the legacy ``ENABLE_PROXY`` module attribute: callers that
    do ``from app.core.proxy import ENABLE_PROXY`` capture a snapshot taken at
    their own import time, which cannot reflect a later settings reload.
    """
    enabled, urls = _load_proxy_config()
    return enabled and bool(urls)


def get_available_proxies() -> list[str]:
    """Return the currently configured, valid proxy URLs."""
    return _load_proxy_config()[1]


def _warn_enabled_but_empty() -> None:
    logger.warning(
        "Proxying is enabled but no valid proxy URLs are configured "
        "(check PROXY_URLS). Requests will go out un-proxied."
    )


async def get_proxy() -> str | None:
    """Return the next proxy URL, or None if proxying is off or unconfigured.

    Example return: ``'http://localhost:8030'``
    """
    enabled, urls = _load_proxy_config()
    if not enabled:
        return None
    if not urls:
        _warn_enabled_but_empty()
        return None

    async with _proxy_lock:
        proxy_url = _next_proxy(urls)
    logger.debug("Selected proxy: %s", mask_proxy(proxy_url))
    return proxy_url


def get_proxy_sync() -> str | None:
    """Synchronous variant of :func:`get_proxy`.

    Returns the first configured proxy rather than advancing the shared
    round-robin cursor, because this is called from synchronous contexts
    without the async lock held.
    """
    enabled, urls = _load_proxy_config()
    if not enabled:
        return None
    if not urls:
        _warn_enabled_but_empty()
        return None

    logger.debug("Selected proxy (sync): %s", mask_proxy(urls[0]))
    return urls[0]


def mask_proxy(url: str | None) -> str:
    """Render a proxy URL safe to log, with the password replaced.

    Proxy URLs carry credentials inline (``http://user:pass@host:port``). Eight
    call sites logged them whole, so turning on DEBUG printed the zone password
    into the application log -- and into anything shipping those logs onward.
    Truncating with ``[:50]`` is not a fix either: it only hides the secret while
    the username happens to be long.
    """
    if not url:
        return "(none)"
    return re.sub(r"://([^:/@]+):[^@]*@", r"://\1:***@", str(url))


def _excluded_hosts() -> tuple[str, ...]:
    """Hosts that must bypass the proxy, from ``NO_PROXY_HOSTS``.

    Read through ``Settings`` rather than ``os.getenv`` for the reason given in
    this module's docstring: pydantic-settings does not export ``.env`` values
    into ``os.environ``, so a getenv read is empty under a bare uvicorn run and
    the exclusion would silently never apply.
    """
    try:
        from app.core.config import get_settings

        raw = getattr(get_settings(), "NO_PROXY_HOSTS", None)
    except Exception:  # pragma: no cover - defensive
        return ()
    if not raw:
        return ()
    values = raw if isinstance(raw, (list, tuple)) else str(raw).split(",")
    return tuple(h.strip().lower().lstrip(".") for h in values if h and h.strip())


def is_host_excluded(url_or_host: str | None) -> bool:
    """True when this target must bypass the proxy.

    Matches on domain suffix, so ``youtube.com`` also covers ``www.youtube.com``
    and ``m.youtube.com`` -- Bright Data refuses CONNECT to all three with
    ``policy_20050``, while the transcript endpoint works fine direct.
    """
    if not url_or_host:
        return False
    host = url_or_host.strip().lower()
    if "//" in host:
        host = host.split("//", 1)[1]
    host = host.split("/", 1)[0].split("@")[-1].split(":")[0]
    for excluded in _excluded_hosts():
        if host == excluded or host.endswith("." + excluded):
            return True
    return False


def proxy_for(url_or_host: str | None = None) -> str | None:
    """The proxy to use for this target, or ``None`` to go direct.

    Callers that route to a mix of targets should prefer this over
    ``get_proxy_sync()``: one blocked domain would otherwise force the whole
    deployment to choose between proxying everything and proxying nothing.
    """
    if is_host_excluded(url_or_host):
        logger.debug("Bypassing proxy for excluded host: %s", url_or_host)
        return None
    return get_proxy_sync()


def rotate_proxy() -> str | None:
    """Advance to the next proxy, for use when the current one fails."""
    enabled, urls = _load_proxy_config()
    if not enabled:
        return None
    if not urls:
        _warn_enabled_but_empty()
        return None

    proxy_url = _next_proxy(urls)
    logger.debug("Rotated to proxy: %s", mask_proxy(proxy_url))
    return proxy_url


def __getattr__(name: str):
    """Resolve legacy module-level constants from Settings on access (PEP 562).

    Several modules do ``from app.core.proxy import ENABLE_PROXY``. Defining
    these lazily rather than as import-time literals means such an import at
    least reads the real configuration, instead of the ``os.getenv`` default
    that was always empty under a bare uvicorn run.

    The value is still a snapshot at the importer's import time, so new code
    should call :func:`is_proxy_enabled` or :func:`get_available_proxies`.
    """
    if name == "ENABLE_PROXY":
        return _load_proxy_config()[0]
    if name in ("PROXY_LIST", "AVAILABLE_PROXIES"):
        return _load_proxy_config()[1]
    if name == "PROXY_URLS":
        return ",".join(_load_proxy_config()[1])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
