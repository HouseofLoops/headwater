"""
Native Google Maps Scraper using Playwright.

This module provides direct Google Maps scraping without requiring
the gosom Docker sidecar. It uses Playwright for browser automation.

Three defects this module previously shipped, and how they are addressed here:

1. **Jobs were global.** ``JobStore`` was a process-local dict with no owner
   parameter, so any valid API key could read, list and delete every other
   caller's jobs, and every job vanished on restart or was invisible to a
   sibling ``--workers`` process. Persistence now goes through
   :mod:`app.services.record_store`, which makes ``owner`` part of the key.

2. **Unbounded browser fan-out.** Nothing capped how many Chromium instances
   could be alive at once; an 11x11 grid search meant 121 of them at ~100 MB
   resident each. A permit from :func:`_browser_semaphore` is now held for the
   whole life of a browser, and :func:`cap_fanout` gives callers a hard
   fan-out ceiling.

3. **Failures became successful empty results.** Every selector was wrapped in
   ``except Exception: pass``, so a Google DOM rotation produced
   ``{"success": true, "places": []}`` -- indistinguishable from a genuine
   no-results search. Optional fields still degrade gracefully, but they are
   now *recorded* (see :meth:`GoogleMapsScraper._optional`), required fields
   raise :class:`PlaceExtractionError`, and a search that finds candidate
   places but extracts none of them raises :class:`SelectorsStaleError`.
"""

import logging
from datetime import datetime

from app.services.google_maps.scraper import GoogleMapsScraper
from app.services.google_maps.scraper_errors import (
    CORE_PLACE_FIELDS,
    REQUIRED_PLACE_FIELDS,
    PlaceExtractionError,
    ScraperError,
    SelectorsStaleError,
)
from app.services.google_maps.scraper_jobs import (
    JOB_NAMESPACE,
    JobStatus,
    JobStore,
    ScrapeJob,
)

# The implementation is split across app.services.google_maps.*; every name
# below is re-exported so existing import paths keep working. The mutable
# limit globals are deliberately NOT re-exported: they live only in
# scraper_limits, and a copy here would be a stale snapshot.
from app.services.google_maps.scraper_limits import (
    DEFAULT_MAX_CONCURRENT_BROWSERS,
    DEFAULT_MAX_FANOUT,
    _browser_semaphore,
    _env_int,
    cap_fanout,
    configure_limits,
    get_max_concurrent_browsers,
    get_max_fanout,
)
from app.services.record_store import (
    RecordStore,
    get_record_store,
    # Re-exported so callers can derive a job owner without also having to know
    # about the record-store module: `from ...google_maps_scraper import
    # owner_id_for_api_key`.
    owner_id_for_api_key,
)

logger = logging.getLogger(__name__)

# Declared explicitly so the re-export above is machine-readable intent rather
# than something a linter has to be told to ignore: `owner_id_for_api_key` is
# part of this module's public surface on purpose.
__all__ = [
    "CORE_PLACE_FIELDS",
    "DEFAULT_MAX_CONCURRENT_BROWSERS",
    "DEFAULT_MAX_FANOUT",
    "JOB_NAMESPACE",
    "REQUIRED_PLACE_FIELDS",
    "GoogleMapsScraper",
    "JobStatus",
    "JobStore",
    "PlaceExtractionError",
    "RecordStore",
    "ScrapeJob",
    "ScraperError",
    "SelectorsStaleError",
    "_browser_semaphore",
    "_env_int",
    "cap_fanout",
    "configure_limits",
    "get_job_store",
    "get_max_concurrent_browsers",
    "get_max_fanout",
    "get_record_store",
    "owner_id_for_api_key",
    "run_scrape_job",
]


# Global job store, backed by the owner-scoped record store.
_job_store = JobStore()


async def get_job_store() -> JobStore:
    """Get the global job store.

    The accessor signature is unchanged, but the returned store is now
    owner-scoped: see :class:`JobStore` for the method signatures, which
    changed (``get``/``delete`` take an owner, ``list_all`` is gone).
    """
    return _job_store


async def run_scrape_job(job: ScrapeJob, proxy: str | None = None):
    """
    Run a scrape job in the background.

    Args:
        job: The job to run. ``job.owner`` must be set.
        proxy: Optional proxy URL

    A scraping failure -- including a stale-selector failure, where Google's
    markup changed and nothing could be parsed -- marks the job FAILED with the
    real error. It must never complete with an empty result list, because a
    caller cannot tell that apart from a genuinely empty area.
    """
    store = await get_job_store()
    scraper = GoogleMapsScraper(proxy=proxy, headless=True)

    try:
        # Update job status
        job.status = JobStatus.RUNNING
        await store.update(job)
        logger.info(f"Starting job {job.id}: {job.query}")

        # Run the search
        results = await scraper.search(
            query=job.query,
            language=job.language,
            max_results=job.max_results,
            zoom=job.zoom,
            geo_coordinates=job.geo_coordinates,
        )

        # Update job with results
        job.results = results
        job.status = JobStatus.COMPLETED
        job.completed_at = datetime.now()
        job.total = len(results)
        job.progress = len(results)
        # An empty result the scraper could not verify is reported as such
        # rather than as a confirmed zero.
        job.empty_unverified = bool(scraper._unverified_empty and not results)
        await store.update(job)

        logger.info(f"Job {job.id} completed with {len(results)} results")

    except SelectorsStaleError as e:
        job.status = JobStatus.FAILED
        job.error = str(e)
        job.selectors_stale = True
        job.completed_at = datetime.now()
        await store.update(job)
        logger.error(
            "Job %s failed: Google Maps selectors are stale (%d attempted, %d extracted): %s",
            job.id,
            e.attempted,
            e.extracted,
            e,
        )

    except Exception as e:
        # Handle failure
        job.status = JobStatus.FAILED
        job.error = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
        job.completed_at = datetime.now()
        await store.update(job)
        logger.error(f"Job {job.id} failed: {e}", exc_info=True)

    finally:
        await scraper.close()
