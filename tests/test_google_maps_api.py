"""Tests for the Google Maps router.

The service layer is mocked throughout: no browser is launched and no HTTP
request leaves the process. The one place that would otherwise touch the
network is DNS resolution inside ``app.core.url_guard``, which is stubbed by
the ``stub_dns`` fixture.

What these tests pin down:

* A caller-supplied lookup URL is validated against the Maps host allow-list
  before it can reach Playwright's ``page.goto()``, and every rejection
  produces the *same bytes* -- no target host, no upstream cause. Rejections
  that differ per cause turn the endpoint into an internal port scanner.
* Job routes are scoped to the caller. Another owner's job is 404, never 403:
  a 403 confirms the id exists and lets a caller enumerate job ids.
* Every route requires an API key and carries the rate-limit dependency.
* The mounted path set and operation ids match the pre-refactor inventory,
  minus the deliberately removed Street View route.
"""

import socket
from typing import Any
from unittest import mock
from unittest.mock import AsyncMock

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.google_maps import google_maps_router
from app.core.auth import get_api_key
from app.core.rate_limiter import rate_limit
from app.services.google_maps_service import google_maps_service
from app.services.record_store import owner_id_for_api_key

# The exact body a rejected URL must produce. Written out literally rather than
# imported so that a change to the constant is a visible test failure and not a
# silently-agreeing tautology.
URL_REJECTED_BODY = b'{"detail":"The supplied URL is not permitted."}'
INTERNAL_ERROR_BODY = b'{"detail":"The request could not be completed."}'

API_KEY_A = "key-owner-a"
API_KEY_B = "key-owner-b"
HEADERS_A = {"X-API-Key": API_KEY_A}
HEADERS_B = {"X-API-Key": API_KEY_B}


# The router's mounted surface as it stood before the split refactor, minus the
# Street View route, which was deliberately deleted. Path + method +
# operationId, because operation ids are what generated clients key off; a
# renamed handler silently breaks them even when the path is untouched.
EXPECTED_ROUTES = [
    ("/api/v1/google-maps/autocomplete", "GET", "autocomplete_api_v1_google_maps_autocomplete_get"),
    ("/api/v1/google-maps/bounding-box-search", "POST", "bounding_box_search_api_v1_google_maps_bounding_box_search_post"),
    ("/api/v1/google-maps/bulk-search", "POST", "bulk_search_api_v1_google_maps_bulk_search_post"),
    ("/api/v1/google-maps/competitors", "POST", "analyze_competitors_api_v1_google_maps_competitors_post"),
    ("/api/v1/google-maps/directions", "GET", "get_directions_get_api_v1_google_maps_directions_get"),
    ("/api/v1/google-maps/directions", "POST", "get_directions_api_v1_google_maps_directions_post"),
    ("/api/v1/google-maps/geocode", "GET", "geocode_get_api_v1_google_maps_geocode_get"),
    ("/api/v1/google-maps/geocode", "POST", "batch_geocode_api_v1_google_maps_geocode_post"),
    ("/api/v1/google-maps/grid-search", "GET", "grid_search_get_api_v1_google_maps_grid_search_get"),
    ("/api/v1/google-maps/grid-search", "POST", "grid_search_api_v1_google_maps_grid_search_post"),
    ("/api/v1/google-maps/health", "GET", "check_health_api_v1_google_maps_health_get"),
    ("/api/v1/google-maps/jobs", "GET", "list_jobs_api_v1_google_maps_jobs_get"),
    ("/api/v1/google-maps/jobs/{job_id}", "DELETE", "delete_job_api_v1_google_maps_jobs__job_id__delete"),
    ("/api/v1/google-maps/jobs/{job_id}", "GET", "get_job_status_api_v1_google_maps_jobs__job_id__get"),
    ("/api/v1/google-maps/jobs/{job_id}/export", "GET", "export_job_results_api_v1_google_maps_jobs__job_id__export_get"),
    ("/api/v1/google-maps/jobs/{job_id}/results", "GET", "get_job_results_api_v1_google_maps_jobs__job_id__results_get"),
    ("/api/v1/google-maps/location-search", "GET", "location_search_get_api_v1_google_maps_location_search_get"),
    ("/api/v1/google-maps/location-search", "POST", "location_search_api_v1_google_maps_location_search_post"),
    ("/api/v1/google-maps/monitors", "GET", "list_monitors_api_v1_google_maps_monitors_get"),
    ("/api/v1/google-maps/monitors", "POST", "create_monitor_api_v1_google_maps_monitors_post"),
    ("/api/v1/google-maps/monitors/{monitor_id}", "DELETE", "delete_monitor_api_v1_google_maps_monitors__monitor_id__delete"),
    ("/api/v1/google-maps/monitors/{monitor_id}", "GET", "get_monitor_api_v1_google_maps_monitors__monitor_id__get"),
    ("/api/v1/google-maps/nearby", "GET", "nearby_search_get_api_v1_google_maps_nearby_get"),
    ("/api/v1/google-maps/nearby", "POST", "nearby_search_api_v1_google_maps_nearby_post"),
    ("/api/v1/google-maps/place/lookup", "POST", "lookup_place_api_v1_google_maps_place_lookup_post"),
    ("/api/v1/google-maps/place/{place_id}", "GET", "get_place_by_id_api_v1_google_maps_place__place_id__get"),
    ("/api/v1/google-maps/place/{place_id}/analytics", "GET", "get_review_analytics_api_v1_google_maps_place__place_id__analytics_get"),
    ("/api/v1/google-maps/place/{place_id}/attributes", "GET", "get_place_attributes_api_v1_google_maps_place__place_id__attributes_get"),
    ("/api/v1/google-maps/place/{place_id}/availability", "GET", "check_availability_api_v1_google_maps_place__place_id__availability_get"),
    ("/api/v1/google-maps/place/{place_id}/history", "GET", "get_place_history_api_v1_google_maps_place__place_id__history_get"),
    ("/api/v1/google-maps/place/{place_id}/menu", "GET", "extract_menu_api_v1_google_maps_place__place_id__menu_get"),
    ("/api/v1/google-maps/place/{place_id}/photos", "GET", "get_place_photos_api_v1_google_maps_place__place_id__photos_get"),
    ("/api/v1/google-maps/place/{place_id}/qa", "GET", "get_place_qa_api_v1_google_maps_place__place_id__qa_get"),
    ("/api/v1/google-maps/place/{place_id}/reviews", "GET", "get_place_reviews_api_v1_google_maps_place__place_id__reviews_get"),
    ("/api/v1/google-maps/search", "GET", "search_places_get_api_v1_google_maps_search_get"),
    ("/api/v1/google-maps/search", "POST", "search_places_api_v1_google_maps_search_post"),
    ("/api/v1/google-maps/webhooks", "GET", "list_webhooks_api_v1_google_maps_webhooks_get"),
    ("/api/v1/google-maps/webhooks", "POST", "register_webhook_api_v1_google_maps_webhooks_post"),
    ("/api/v1/google-maps/webhooks/{webhook_id}", "DELETE", "delete_webhook_api_v1_google_maps_webhooks__webhook_id__delete"),
]


# ---------------------------------------------------------------------------
# App / client fixtures
# ---------------------------------------------------------------------------

def build_app() -> FastAPI:
    """Mount the router exactly as ``main.create_application`` does.

    Same prefixes and the same router-level ``get_api_key`` dependency, so the
    paths, operation ids and auth behaviour under test are the production ones.
    """
    app = FastAPI()
    v1_router = APIRouter(prefix="/api/v1")
    v1_router.include_router(
        google_maps_router,
        prefix="/google-maps",
        tags=["Google Maps API"],
        dependencies=[Depends(get_api_key)],
    )
    app.include_router(v1_router)
    return app


@pytest.fixture
def app() -> FastAPI:
    return build_app()


@pytest.fixture
def known_api_keys():
    """Make API_KEY_A and API_KEY_B valid for the duration of a test."""
    from app.core import auth
    from app.core.config import get_settings

    # Patch the snapshot accessor rather than the state tuple: auth rebuilds
    # its state whenever the Settings identity changes, which would silently
    # discard a patched tuple mid-test.
    auth_state = auth._AuthState(
        settings=get_settings(),
        keys=frozenset({API_KEY_A, API_KEY_B}),
        metadata={},
    )
    with mock.patch.object(auth, "_auth_snapshot", return_value=auth_state):
        yield


@pytest.fixture
def client(app, known_api_keys) -> TestClient:
    """Authenticated client with rate limiting disabled.

    Rate limiting needs Redis, and its *presence* is asserted structurally in
    ``test_every_route_has_rate_limiting`` rather than by exhausting a bucket.
    """
    app.dependency_overrides[rate_limit] = lambda: None
    return TestClient(app)


@pytest.fixture
def stub_dns():
    """Resolve allow-listed Google hosts to a fixed public address, offline.

    Without this the URL guard would make a real DNS query for the accepted
    case. Anything not on the allow-list raises, exactly as an unknown name
    would.
    """
    public_ip = "142.250.190.68"
    resolvable = {
        "www.google.com",
        "maps.google.com",
        "www.google.co.uk",
        "goo.gl",
        "maps.app.goo.gl",
    }

    def fake_getaddrinfo(host, port, *args, **kwargs):
        if host in resolvable:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (public_ip, port))]
        raise socket.gaierror("Name or service not known")

    with mock.patch("app.core.url_guard.socket.getaddrinfo", fake_getaddrinfo):
        yield


# ---------------------------------------------------------------------------
# CRT-3: SSRF in lookup_place
# ---------------------------------------------------------------------------

# Every one of these previously reached Playwright's page.goto() inside the
# container network, with the resulting DOM returned to the caller.
HOSTILE_URLS = [
    "http://127.0.0.1/",
    "https://127.0.0.1/",
    "http://169.254.169.254/latest/meta-data/",
    "https://169.254.169.254/latest/meta-data/",
    "file:///etc/passwd",
    "https://evil.com/",
    "https://www.google.com@evil.com/",
    "https://www.google.com.evil.com/",
    "http://localhost:6379/",
    "https://www.google.com:8080/maps/place/x",
    "gopher://127.0.0.1:6379/_INFO",
    "//127.0.0.1/",
    "/etc/passwd",
]


@pytest.mark.parametrize("hostile_url", HOSTILE_URLS)
def test_lookup_place_rejects_hostile_url(client, stub_dns, hostile_url):
    """A URL outside the Maps allow-list is refused before the service runs."""
    with mock.patch.object(
        google_maps_service, "lookup_place", new=AsyncMock()
    ) as lookup:
        response = client.post(
            "/api/v1/google-maps/place/lookup",
            json={"url": hostile_url},
            headers=HEADERS_A,
        )

    assert response.status_code == 400
    assert response.content == URL_REJECTED_BODY
    # The sink must never be reached, not even to be told the fetch failed.
    lookup.assert_not_called()


def test_lookup_place_rejection_body_is_byte_identical(client, stub_dns):
    """Different causes must be indistinguishable to the caller.

    "host not allowed", "scheme not permitted" and "resolves to a private
    address" arriving as different bodies is what let the sibling News endpoint
    be used as an internal port-scan oracle.
    """
    bodies = set()
    statuses = set()
    with mock.patch.object(google_maps_service, "lookup_place", new=AsyncMock()):
        for hostile_url in HOSTILE_URLS:
            response = client.post(
                "/api/v1/google-maps/place/lookup",
                json={"url": hostile_url},
                headers=HEADERS_A,
            )
            bodies.add(response.content)
            statuses.add(response.status_code)

    assert bodies == {URL_REJECTED_BODY}
    assert statuses == {400}


def test_lookup_place_rejection_leaks_no_target_or_cause(client, stub_dns):
    """The body names neither the target nor why the fetch was refused."""
    forbidden_fragments = [
        b"127.0.0.1",
        b"169.254",
        b"localhost",
        b"evil.com",
        b"etc/passwd",
        b"6379",
        b"8080",
        b"allow-list",
        b"scheme",
        b"DNS",
        b"resolve",
        b"refused",
        b"timed out",
        b"routable",
    ]
    with mock.patch.object(google_maps_service, "lookup_place", new=AsyncMock()):
        for hostile_url in HOSTILE_URLS:
            body = client.post(
                "/api/v1/google-maps/place/lookup",
                json={"url": hostile_url},
                headers=HEADERS_A,
            ).content
            for fragment in forbidden_fragments:
                assert fragment not in body, (hostile_url, fragment)


def test_lookup_place_accepts_a_real_maps_url(client, stub_dns):
    """A genuine Google Maps URL still reaches the service, normalised."""
    good_url = "https://www.google.com/maps/place/Statue+of+Liberty"
    with mock.patch.object(
        google_maps_service,
        "lookup_place",
        new=AsyncMock(return_value={"place": {"name": "Statue of Liberty"}}),
    ) as lookup:
        response = client.post(
            "/api/v1/google-maps/place/lookup",
            json={"url": good_url},
            headers=HEADERS_A,
        )

    assert response.status_code == 200
    assert response.json()["place"]["name"] == "Statue of Liberty"
    lookup.assert_awaited_once()
    assert lookup.await_args.kwargs["url"] == good_url


def test_lookup_place_still_accepts_a_place_id(client, stub_dns):
    """Guarding ``url`` must not break the place_id path."""
    with mock.patch.object(
        google_maps_service,
        "lookup_place",
        new=AsyncMock(return_value={"place": {"name": "Somewhere"}}),
    ) as lookup:
        response = client.post(
            "/api/v1/google-maps/place/lookup",
            json={"place_id": "0x89c259af18b60947:0x8c5e3c1d36e36e0a"},
            headers=HEADERS_A,
        )

    assert response.status_code == 200
    assert lookup.await_args.kwargs["place_id"].startswith("0x89c259af")


def test_lookup_place_without_url_or_place_id_is_400(client, stub_dns):
    response = client.post(
        "/api/v1/google-maps/place/lookup", json={}, headers=HEADERS_A
    )
    assert response.status_code == 400


def test_unrelated_validation_errors_keep_their_422(client, stub_dns):
    """The URL guard must not swallow ordinary request validation."""
    response = client.post(
        "/api/v1/google-maps/place/lookup",
        json={"url": 12345},
        headers=HEADERS_A,
    )
    assert response.status_code == 422


def test_monitor_url_is_guarded_too(client, stub_dns):
    """``MonitorRequest.url`` feeds the same sink, on a repeating schedule."""
    with mock.patch.object(
        google_maps_service, "create_monitor", new=AsyncMock()
    ) as create:
        response = client.post(
            "/api/v1/google-maps/monitors",
            json={"url": "http://169.254.169.254/latest/meta-data/"},
            headers=HEADERS_A,
        )

    assert response.status_code == 400
    assert response.content == URL_REJECTED_BODY
    create.assert_not_called()


# ---------------------------------------------------------------------------
# Job ownership
# ---------------------------------------------------------------------------

OWNER_A = owner_id_for_api_key(API_KEY_A)
OWNER_B = owner_id_for_api_key(API_KEY_B)

JOB_OF_A = "job-owned-by-a"
JOB_OF_B = "job-owned-by-b"

_NOT_FOUND: dict[str, Any] = {
    "error": True,
    "status_code": 404,
    "message": "Job not found",
}


def test_two_api_keys_map_to_two_owners():
    """The whole scoping scheme rests on this."""
    assert OWNER_A != OWNER_B


@pytest.fixture
def owner_scoped_service():
    """Stand in for the owner-scoped store the service layer now wraps.

    Mirrors ``RecordStore``: a record is addressed by ``(owner, id)``, so a
    lookup with the wrong owner is not "forbidden", it simply does not exist.
    """
    jobs = {JOB_OF_A: OWNER_A, JOB_OF_B: OWNER_B}

    async def get_job_status(job_id: str, owner: str | None = None):
        if jobs.get(job_id) != owner:
            return dict(_NOT_FOUND)
        return {"status": "completed", "progress": 100}

    async def get_job_results(
        job_id: str, owner: str | None = None, format: str = "json"
    ):
        if jobs.get(job_id) != owner:
            return dict(_NOT_FOUND)
        return {"results": [{"name": "A place"}]}

    async def delete_job(job_id: str, owner: str | None = None):
        if jobs.get(job_id) != owner:
            return dict(_NOT_FOUND)
        del jobs[job_id]
        return {"success": True, "job_id": job_id}

    async def list_jobs(
        owner: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        return [{"id": jid} for jid, own in jobs.items() if own == owner]

    with mock.patch.multiple(
        google_maps_service,
        get_job_status=get_job_status,
        get_job_results=get_job_results,
        delete_job=delete_job,
        list_jobs=list_jobs,
        process_place_data=lambda raw: list(raw),
    ):
        yield jobs


def test_list_jobs_returns_only_the_callers_jobs(client, owner_scoped_service):
    body_a = client.get("/api/v1/google-maps/jobs", headers=HEADERS_A).json()
    body_b = client.get("/api/v1/google-maps/jobs", headers=HEADERS_B).json()

    assert [j["id"] for j in body_a["jobs"]] == [JOB_OF_A]
    assert [j["id"] for j in body_b["jobs"]] == [JOB_OF_B]


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/google-maps/jobs/{job_id}",
        "/api/v1/google-maps/jobs/{job_id}/results",
        "/api/v1/google-maps/jobs/{job_id}/export",
    ],
)
def test_cross_owner_read_is_404_not_403(client, owner_scoped_service, path):
    """404, never 403: a 403 confirms the id exists and enables enumeration."""
    response = client.get(path.format(job_id=JOB_OF_B), headers=HEADERS_A)

    assert response.status_code == 404
    assert response.status_code != 403


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/google-maps/jobs/{job_id}",
        "/api/v1/google-maps/jobs/{job_id}/results",
        "/api/v1/google-maps/jobs/{job_id}/export",
    ],
)
def test_owner_can_read_their_own_job(client, owner_scoped_service, path):
    response = client.get(path.format(job_id=JOB_OF_A), headers=HEADERS_A)
    assert response.status_code == 200


def test_cross_owner_delete_is_404_and_does_not_delete(
    client, owner_scoped_service
):
    response = client.delete(
        f"/api/v1/google-maps/jobs/{JOB_OF_B}", headers=HEADERS_A
    )

    assert response.status_code == 404
    assert JOB_OF_B in owner_scoped_service, "B's job was deleted by A"


def test_owner_can_delete_their_own_job(client, owner_scoped_service):
    response = client.delete(
        f"/api/v1/google-maps/jobs/{JOB_OF_A}", headers=HEADERS_A
    )

    assert response.status_code == 200
    assert JOB_OF_A not in owner_scoped_service


def test_a_missing_job_and_another_owners_job_are_indistinguishable(
    client, owner_scoped_service
):
    """Otherwise the 404/other split is itself an id-enumeration oracle."""
    other = client.get(
        f"/api/v1/google-maps/jobs/{JOB_OF_B}", headers=HEADERS_A
    )
    absent = client.get(
        "/api/v1/google-maps/jobs/no-such-job-at-all", headers=HEADERS_A
    )

    assert other.status_code == absent.status_code == 404
    assert other.content == absent.content


def test_job_routes_pass_the_callers_owner_id(client, owner_scoped_service):
    """Pin the exact argument shape the service layer must accept."""
    recorded = {}

    async def record(job_id: str, owner: str | None = None):
        recorded["job_id"] = job_id
        recorded["owner"] = owner
        return {"status": "completed"}

    with mock.patch.object(google_maps_service, "get_job_status", record):
        client.get(f"/api/v1/google-maps/jobs/{JOB_OF_A}", headers=HEADERS_A)

    assert recorded == {"job_id": JOB_OF_A, "owner": OWNER_A}


def test_created_jobs_are_stamped_with_the_callers_owner(client):
    """A job with no owner could never be read back under the scoped store."""
    recorded = {}

    async def create_search_job(**kwargs):
        recorded.update(kwargs)
        return {"job_id": "new-job"}

    with mock.patch.multiple(
        google_maps_service,
        health_check=AsyncMock(return_value={"healthy": True}),
        create_search_job=create_search_job,
    ):
        response = client.post(
            "/api/v1/google-maps/search?wait_for_results=false",
            json={"query": "coffee in Portland"},
            headers=HEADERS_A,
        )

    assert response.status_code == 200
    assert recorded["owner"] == OWNER_A


# ---------------------------------------------------------------------------
# Authentication and rate limiting on every route
# ---------------------------------------------------------------------------

MAPS_PREFIX = "/api/v1/google-maps"


def _mounted_routes(app: FastAPI):
    """Yield (route, path, method) for the Maps routes only.

    ``app.routes`` holds lazy ``_IncludedRouter`` wrappers rather than flat
    ``APIRoute`` objects in this FastAPI version, so the same helpers FastAPI's
    own schema generation uses are used to materialise them. The materialised
    route carries the dependencies contributed by every enclosing
    ``include_router`` call, which is exactly what these tests need to see --
    ``/health`` gets its API key requirement that way and no other.

    FastAPI's own ``/openapi.json`` and ``/docs`` share the app and are not
    this router's to police, hence the prefix filter.
    """
    from fastapi.openapi.utils import _get_api_route_for_openapi
    from fastapi.routing import iter_route_contexts

    for context in iter_route_contexts(app.routes):
        route = _get_api_route_for_openapi(context)
        if route is None or not route.path.startswith(MAPS_PREFIX):
            continue
        for method in sorted(set(route.methods) - {"HEAD", "OPTIONS"}):
            yield route, route.path, method


def test_every_route_requires_an_api_key(app, known_api_keys):
    """No route may answer without a key -- including /health."""
    app.dependency_overrides[rate_limit] = lambda: None
    client = TestClient(app)

    checked = 0
    for _route, path, method in _mounted_routes(app):
        concrete = path
        while "{" in concrete:
            start = concrete.index("{")
            end = concrete.index("}", start)
            concrete = concrete[:start] + "x" + concrete[end + 1 :]

        response = client.request(method, concrete, json={})
        assert response.status_code in (401, 403), (
            f"{method} {path} answered {response.status_code} with no API key"
        )
        checked += 1

    assert checked == len(EXPECTED_ROUTES)


def _dependency_calls(route) -> set:
    """Every dependency callable reachable from a route, transitively."""
    found = set()

    def walk(dependant):
        for sub in dependant.dependencies:
            if sub.call is not None:
                found.add(sub.call)
            walk(sub)

    walk(route.dependant)
    return found


def test_every_route_has_rate_limiting(app):
    """The job, monitor and webhook routes previously had none at all."""
    missing = [
        f"{method} {path}"
        for route, path, method in _mounted_routes(app)
        if rate_limit not in _dependency_calls(route)
    ]
    assert missing == []


def test_every_route_has_the_api_key_dependency(app):
    missing = [
        f"{method} {path}"
        for route, path, method in _mounted_routes(app)
        if get_api_key not in _dependency_calls(route)
    ]
    assert missing == []


# ---------------------------------------------------------------------------
# OpenAPI surface
# ---------------------------------------------------------------------------

def _route_inventory(app: FastAPI):
    paths = app.openapi()["paths"]
    return sorted(
        (path, method.upper(), operation.get("operationId"))
        for path, methods in paths.items()
        for method, operation in methods.items()
    )


def test_openapi_surface_is_unchanged_except_for_street_view(app):
    """The split is a refactor: paths and operation ids must not move."""
    assert _route_inventory(app) == sorted(EXPECTED_ROUTES)


def test_street_view_route_is_gone(app):
    inventory = _route_inventory(app)
    assert not [entry for entry in inventory if "streetview" in entry[0]]
    assert not [
        entry for entry in inventory if "streetview" in (entry[2] or "")
    ]


def test_street_view_returns_404(client):
    response = client.get(
        "/api/v1/google-maps/place/abc/streetview", headers=HEADERS_A
    )
    assert response.status_code == 404


def test_street_view_is_absent_from_the_openapi_schema(app):
    """No leftover schema, description or example mentioning Street View."""
    assert "streetview" not in str(app.openapi()).lower()
    assert "street view" not in str(app.openapi()).lower()


# ---------------------------------------------------------------------------
# Error bodies
# ---------------------------------------------------------------------------

def test_unexpected_service_failure_does_not_leak_internals(client):
    """``str(exc)`` from Playwright or Redis must not reach the caller."""
    boom = RuntimeError("connect ECONNREFUSED redis://cache.internal:6379")
    with mock.patch.object(
        google_maps_service, "get_place_by_id", new=AsyncMock(side_effect=boom)
    ):
        response = client.get(
            "/api/v1/google-maps/place/some-place-id", headers=HEADERS_A
        )

    assert response.status_code == 500
    assert response.content == INTERNAL_ERROR_BODY
    assert b"redis" not in response.content
    assert b"cache.internal" not in response.content
    assert b"6379" not in response.content


UPSTREAM_LEAK = (
    "playwright: connect ECONNREFUSED proxy.internal:8118 while loading "
    "https://maps.google.com/maps/place/x"
)


def test_service_reported_failure_does_not_leak_its_message(client):
    """The service puts ``str(exc)`` in ``message``; it must not be returned.

    This is the same leak as the catch-all handlers, on the path where the
    service returns an error dict instead of raising.
    """
    with mock.patch.object(
        google_maps_service,
        "get_place_by_id",
        new=AsyncMock(return_value={"error": True, "message": UPSTREAM_LEAK}),
    ):
        response = client.get(
            "/api/v1/google-maps/place/some-place-id", headers=HEADERS_A
        )

    assert response.status_code == 500
    for fragment in (b"proxy.internal", b"8118", b"ECONNREFUSED", b"playwright"):
        assert fragment not in response.content


def test_upstream_cannot_choose_our_status_code(client):
    """An unfiltered upstream status is itself a per-cause signal."""
    with mock.patch.object(
        google_maps_service,
        "get_place_by_id",
        new=AsyncMock(
            return_value={"error": True, "status_code": 418, "message": UPSTREAM_LEAK}
        ),
    ):
        response = client.get(
            "/api/v1/google-maps/place/some-place-id", headers=HEADERS_A
        )

    assert response.status_code == 500


def test_upstream_404_still_reaches_the_caller(client):
    """Clamping status codes must not turn a real 'not found' into a 500."""
    with mock.patch.object(
        google_maps_service,
        "get_place_by_id",
        new=AsyncMock(
            return_value={"error": True, "status_code": 404, "message": "no such place"}
        ),
    ):
        response = client.get(
            "/api/v1/google-maps/place/some-place-id", headers=HEADERS_A
        )

    assert response.status_code == 404
    assert b"no such place" not in response.content


def test_failed_job_reports_a_constant_detail(client):
    """A failed job says so, and says nothing about why.

    A truthy ``error`` on the status dict is consumed by the error check above
    this branch, so the reason arrives (if at all) on other keys; none of them
    belong in the response.
    """
    with mock.patch.object(
        google_maps_service,
        "get_job_status",
        new=AsyncMock(return_value={"status": "failed", "detail": UPSTREAM_LEAK}),
    ):
        response = client.get(
            f"/api/v1/google-maps/jobs/{JOB_OF_A}/results", headers=HEADERS_A
        )

    assert response.status_code == 500
    assert response.json()["detail"] == "Job failed."
    assert b"proxy.internal" not in response.content


def test_malformed_results_payload_is_an_error_not_an_empty_success(client):
    """A bad upstream shape used to become 200 with zero results."""
    with mock.patch.multiple(
        google_maps_service,
        get_job_status=AsyncMock(return_value={"status": "completed"}),
        get_job_results=AsyncMock(
            return_value={"results": {"unexpected": "shape"}}
        ),
        process_place_data=lambda raw: list(raw),
    ):
        response = client.get(
            f"/api/v1/google-maps/jobs/{JOB_OF_A}/results", headers=HEADERS_A
        )

    assert response.status_code == 500
    assert response.content == INTERNAL_ERROR_BODY


class TestOwnerIsPropagatedToService:
    """Regression guard for a cross-tenant hole created at the router/service seam.

    The service methods accept `api_key` and derive an owner partition from it.
    The router authenticated the caller and then called those methods WITHOUT
    passing it, so every caller resolved to the same `anonymous` partition and
    any user could read or delete any other user's monitors and webhooks.

    Both halves were individually correct; only the seam was wrong. These
    tests assert the argument actually crosses it.
    """

    MONITOR_CALLS = [
        ("post", "/api/v1/google-maps/monitors", "create_monitor",
         {"json": {"place_id": "ChIJtest", "check_interval_hours": 24}}),
        ("get", "/api/v1/google-maps/monitors", "list_monitors", {}),
        ("get", "/api/v1/google-maps/monitors/m1", "get_monitor", {}),
        ("delete", "/api/v1/google-maps/monitors/m1", "delete_monitor", {}),
        ("post", "/api/v1/google-maps/webhooks", "register_webhook",
         {"json": {"url": "https://example.com/hook", "events": ["place.changed"]}}),
        ("get", "/api/v1/google-maps/webhooks", "list_webhooks", {}),
        ("delete", "/api/v1/google-maps/webhooks/w1", "delete_webhook", {}),
    ]

    @pytest.mark.parametrize(
        "method,path,service_method,kwargs",
        MONITOR_CALLS,
        ids=[c[2] for c in MONITOR_CALLS],
    )
    def test_api_key_reaches_the_service(self, client, method, path, service_method, kwargs):
        from app.services import google_maps_service as svc_module

        stub = mock.AsyncMock(return_value={"monitors": [], "webhooks": [], "total": 0})
        with mock.patch.object(
            svc_module.google_maps_service, service_method, stub
        ):
            getattr(client, method)(
                path, headers={"X-API-Key": API_KEY_A}, **kwargs
            )

        assert stub.await_count == 1, f"{service_method} was not called"
        passed = stub.await_args.kwargs.get("api_key")
        assert passed == API_KEY_A, (
            f"{service_method} received api_key={passed!r}; without the real key "
            "every caller collapses into the 'anonymous' owner partition and "
            "monitors/webhooks leak across tenants"
        )

    def test_place_history_receives_api_key(self, client):
        from app.services import google_maps_service as svc_module

        stub = mock.AsyncMock(return_value={"history": [], "monitored": False})
        with mock.patch.object(svc_module.google_maps_service, "get_place_history", stub):
            client.get(
                "/api/v1/google-maps/place/ChIJtest/history",
                headers={"X-API-Key": API_KEY_A},
            )

        assert stub.await_count == 1
        assert stub.await_args.kwargs.get("api_key") == API_KEY_A
