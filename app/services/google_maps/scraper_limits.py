"""
Browser-concurrency and fan-out limits for the Google Maps scraper.

The module-level limit state (``_max_concurrent_browsers``, ``_max_fanout``
and the per-loop semaphores) lives here and only here; the facade
re-exports the accessor functions, never the mutable globals.
"""
import asyncio
import logging
import os
import weakref
from typing import Optional, List, Any, Sequence, TypeVar

# Logs under the facade module's name so log routing and filters keyed on
# ``app.services.google_maps_scraper`` are unaffected by the split.
logger = logging.getLogger("app.services.google_maps_scraper")

T = TypeVar("T")
# -- Concurrency limits -------------------------------------------------------
#
# Chromium is roughly 100 MB resident per instance. These are deliberately
# small: the failure mode of too low a cap is a slow request, the failure mode
# of too high a cap is the container being OOM-killed mid-request.
#
# Configured via environment rather than ``Settings`` so that this module does
# not have to be edited in lockstep with ``app/core/config.py``. If/when a
# ``Settings`` field is added, point these defaults at it.

DEFAULT_MAX_CONCURRENT_BROWSERS = 4

#: Hard ceiling on grid/bulk fan-out, regardless of what a caller requests.
#: An 11x11 grid is 121 points; that is allowed as a *request*, but the number
#: of points actually visited is clamped to this.
DEFAULT_MAX_FANOUT = 25


def _env_int(name: str, default: int) -> int:
    """Read a positive int from the environment, falling back to ``default``.

    A malformed or non-positive value is a configuration error, not a licence
    to run unbounded, so it logs and uses the safe default.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s=%r is not an integer; using %d", name, raw, default)
        return default
    if value < 1:
        logger.warning("%s=%d must be >= 1; using %d", name, value, default)
        return default
    return value


_max_concurrent_browsers = _env_int(
    "GOOGLE_MAPS_MAX_CONCURRENT_BROWSERS", DEFAULT_MAX_CONCURRENT_BROWSERS
)
_max_fanout = _env_int("GOOGLE_MAPS_MAX_FANOUT", DEFAULT_MAX_FANOUT)

# Semaphores are per event loop: a single module-level Semaphore binds to the
# first loop that awaits it, and reusing it from another loop (pytest creates
# one per test, uvicorn one per worker) raises or silently fails to bound.
_browser_semaphores: "weakref.WeakKeyDictionary[Any, asyncio.Semaphore]" = (
    weakref.WeakKeyDictionary()
)


def get_max_concurrent_browsers() -> int:
    """Current cap on simultaneously-live Chromium instances in this process."""
    return _max_concurrent_browsers


def get_max_fanout() -> int:
    """Current hard ceiling on grid/bulk fan-out."""
    return _max_fanout


def configure_limits(
    *,
    max_concurrent_browsers: Optional[int] = None,
    max_fanout: Optional[int] = None,
) -> None:
    """Override the concurrency limits (startup configuration and tests).

    Changing ``max_concurrent_browsers`` discards the existing semaphores, so
    the new cap applies to browsers acquired from now on.
    """
    global _max_concurrent_browsers, _max_fanout
    if max_concurrent_browsers is not None:
        if max_concurrent_browsers < 1:
            raise ValueError("max_concurrent_browsers must be >= 1")
        _max_concurrent_browsers = max_concurrent_browsers
        _browser_semaphores.clear()
    if max_fanout is not None:
        if max_fanout < 1:
            raise ValueError("max_fanout must be >= 1")
        _max_fanout = max_fanout


def _browser_semaphore() -> asyncio.Semaphore:
    """Return this event loop's browser-acquisition semaphore."""
    loop = asyncio.get_running_loop()
    sem = _browser_semaphores.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(_max_concurrent_browsers)
        _browser_semaphores[loop] = sem
    return sem


def cap_fanout(items: Sequence[T], *, kind: str = "fan-out") -> List[T]:
    """Clamp a fan-out list (grid points, bulk queries) to the hard ceiling.

    Callers that build a work list -- ``grid_search``, ``bulk_search`` -- must
    pass it through here before iterating. Truncating loudly is preferable to
    either silently accepting 121 browser launches or rejecting the request
    outright, but the log line makes the truncation auditable.
    """
    limit = _max_fanout
    if len(items) <= limit:
        return list(items)
    logger.warning(
        "%s requested %d points; truncated to the %d-point limit "
        "(raise GOOGLE_MAPS_MAX_FANOUT to allow more)",
        kind,
        len(items),
        limit,
    )
    return list(items[:limit])
