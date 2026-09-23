"""
Google Maps implementation modules.

The public entry points remain ``app.services.google_maps_service`` and
``app.services.google_maps_scraper``; those modules re-export everything
defined here. This package holds the pieces they were split into:

Scraper (``google_maps_scraper``):
    - scraper_limits: browser-concurrency and fan-out caps
    - scraper_errors: failure signals and required/core place fields
    - scraper_jobs: ScrapeJob, JobStatus and the owner-scoped JobStore
    - scraper: GoogleMapsScraper (browser lifecycle, search, result feed)
    - scraper_place_details: place-panel extraction (mixin)

Service (``google_maps_service``), each a mixin of GoogleMapsService:
    - service_area_search: nearby/grid/bounding-box/location/bulk search
    - service_place_content: reviews, photos, Q&A, autocomplete,
      analytics, geocoding, attributes, history
    - service_monitors: monitor and webhook delegation
    - service_directions: directions scraping and route parsing
    - service_menu: menu extraction
    - service_reservations: reservation availability
"""

