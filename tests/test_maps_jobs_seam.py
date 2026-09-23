"""End-to-end tests for the Maps jobs router -> service -> store seam.

These exist because of a bug that every other test missed.

The router was written to call ``list_jobs(owner=...)``,
``get_job_status(job_id, owner=...)`` and friends. The service still had its
pre-remediation signatures and called ``store.list_all()`` -- a method the
owner-scoped store deliberately removed, because returning every tenant's jobs
regardless of caller *was* the vulnerability. So every jobs endpoint raised
TypeError or AttributeError at runtime.

Nothing caught it. The router tests mock the service, the service tests mock
the store, and each layer is correct in isolation; only the seam between them
was broken. CodeQL found it statically (``py/call/wrong-named-argument``).

So these tests mock **only the browser** -- the one genuinely external thing --
and let the service and the store run for real.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.record_store import owner_id_for_api_key

OWNER_A = owner_id_for_api_key("key-alpha")
OWNER_B = owner_id_for_api_key("key-bravo")

pytestmark = pytest.mark.asyncio


@pytest.fixture
def svc():
    """Real service + real scraper module + real store on its memory backend.

    ``_ensure_initialized`` is stubbed because it would launch a browser, but
    stubbing it alone leaves ``_scraper_module`` as None -- so the module is
    wired in explicitly.
    """
    from app.services import google_maps_scraper as scraper
    from app.services.google_maps_service import google_maps_service
    from app.services.record_store import RecordStore, get_record_store

    store = get_record_store(scraper.JOB_NAMESPACE)
    store._memory.clear()

    with (
        patch.object(RecordStore, "_get_redis", return_value=None),
        patch.object(google_maps_service, "_ensure_initialized", AsyncMock()),
        patch.object(google_maps_service, "_scraper_module", scraper),
    ):
        yield google_maps_service

    store._memory.clear()


async def _make_job(owner, job_id, status="completed"):
    """Insert a job through the real store."""
    from app.services import google_maps_scraper as scraper

    store = await scraper.get_job_store()
    job = scraper.ScrapeJob(id=job_id, name=f"job-{job_id}", query="coffee", owner=owner)
    job.status = scraper.JobStatus(status)
    await store.create(job)
    return job


class TestServiceAcceptsWhatTheRouterPasses:
    """The exact keyword arguments the router uses must be accepted.

    A mismatch here is a runtime TypeError on a live endpoint, and it is
    invisible to any test that mocks the service.
    """

    async def test_list_jobs_accepts_owner(self, svc):
        await _make_job(OWNER_A, "job-a1")
        result = await svc.list_jobs(owner=OWNER_A, status=None, limit=50, offset=0)

        assert isinstance(result, list), result
        assert [j["id"] for j in result] == ["job-a1"]

    async def test_get_job_status_accepts_owner(self, svc):
        await _make_job(OWNER_A, "job-a2")
        result = await svc.get_job_status("job-a2", owner=OWNER_A)

        assert result.get("job_id") == "job-a2"
        assert not result.get("error")

    async def test_delete_job_accepts_owner(self, svc):
        await _make_job(OWNER_A, "job-a3")
        result = await svc.delete_job("job-a3", owner=OWNER_A)

        assert result.get("success") is True

    async def test_get_job_results_accepts_owner_and_format(self, svc):
        await _make_job(OWNER_A, "job-a4", status="completed")
        result = await svc.get_job_results("job-a4", owner=OWNER_A, format="json")

        # Real results or a structured error -- never a TypeError.
        assert isinstance(result, dict)
        assert result.get("message") != "Job not found"


class TestOwnerIsolationThroughTheRealStore:
    """Isolation must hold through service + store, not only inside the store."""

    async def test_owner_b_cannot_see_owner_a_jobs(self, svc):
        await _make_job(OWNER_A, "job-secret")
        assert await svc.list_jobs(owner=OWNER_B) == []

    async def test_owner_b_gets_404_for_owner_a_job(self, svc):
        await _make_job(OWNER_A, "job-secret-2")
        result = await svc.get_job_status("job-secret-2", owner=OWNER_B)

        # 404, not 403: a 403 would confirm the id exists.
        assert result.get("error") is True
        assert result.get("status_code") == 404

    async def test_owner_b_cannot_delete_owner_a_job(self, svc):
        await _make_job(OWNER_A, "job-secret-3")
        result = await svc.delete_job("job-secret-3", owner=OWNER_B)
        still_there = await svc.get_job_status("job-secret-3", owner=OWNER_A)

        assert result.get("status_code") == 404
        assert still_there.get("job_id") == "job-secret-3"


class TestStoreHasNoUnscopedListing:
    """list_all() must stay gone -- it returned every tenant's jobs."""

    async def test_list_all_is_not_reintroduced(self):
        from app.services import google_maps_scraper as scraper

        store = await scraper.get_job_store()
        assert not hasattr(store, "list_all"), (
            "JobStore.list_all() is back; it ignores the caller and returns "
            "every owner's jobs, which is the bug the owner-scoped store fixed"
        )
