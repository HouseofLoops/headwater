"""Google Maps API package.

Provides FastAPI endpoints for Google Maps data extraction using Playwright.

The endpoints used to live in one 2,900-line module, which is how two
fabricated routes and an unguarded SSRF sink went unnoticed for as long as they
did. They are now split by concern, one module per group, and composed here
into the single ``google_maps_router`` the application mounts:

    common     shared error bodies, the URL validator, the safe route class
    schemas    every request and response model
    health     scraper health
    search     text, nearby, grid, bounding box, location, bulk, autocomplete
    jobs       async job management (owner-scoped)
    places     single-place detail endpoints
    analytics  review analytics and competitor comparison
    monitors   place monitors and webhooks
    geo        directions and geocoding

The split is a refactor and nothing more: paths, handler names and therefore
operation ids are unchanged, which ``tests/test_google_maps_api.py`` asserts
against a recorded inventory.
"""

from fastapi import APIRouter

from app.api.google_maps import (
    analytics,
    geo,
    health,
    jobs,
    monitors,
    places,
    search,
)
from app.api.google_maps.common import SafeUrlValidationRoute

google_maps_router = APIRouter(tags=["Google Maps API"], route_class=SafeUrlValidationRoute)

# Mounted in the order the endpoints were originally declared. None of the
# paths collide, so order does not affect matching -- it is kept only so a
# reader of the OpenAPI schema sees the same grouping as before.
for _endpoints in (health, search, jobs, places, analytics, monitors, geo):
    google_maps_router.include_router(_endpoints.router)

__all__ = ["google_maps_router"]
