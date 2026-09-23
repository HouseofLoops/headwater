"""Shared building blocks for the Google Maps endpoint modules.

Every endpoint module in this package needs the same three things: the fixed
error bodies, the route class that stops a rejected URL from becoming an
oracle, and the validator that decides which caller-supplied URLs may be
fetched at all.
"""
import logging
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.core.log_safety import scrub
from app.core.url_guard import MAPS_ALLOWED_HOSTS, UrlNotAllowed, validate_outbound_url
from app.services.google_maps_service import google_maps_service

logger = logging.getLogger(__name__)


# ============================================================================
# Error responses
# ============================================================================
#
# Two rules govern everything this router puts in an error body:
#
# 1. A caller-supplied URL that fails validation always produces the SAME
#    bytes, whatever the cause. "host not allowed", "scheme not permitted",
#    "resolves to a private address" and "DNS failed" are all
#    ``URL_REJECTED_DETAIL``. Distinguishable rejections turn this endpoint
#    into an internal port scanner: the attacker learns which hosts exist and
#    which ports answer purely from which error comes back. That is exactly
#    how the sibling News endpoint behaved before it was fixed.
#
# 2. An unexpected server-side exception never has ``str(exc)`` returned to the
#    caller. Playwright, Redis and DNS exception strings carry internal host
#    names, file paths and container addresses. The detail is logged; the
#    caller gets a fixed sentence.

URL_REJECTED_DETAIL = "The supplied URL is not permitted."

# Private sentinel. The Pydantic validator cannot itself choose the HTTP
# response, so it raises a ValueError carrying this marker and
# ``SafeUrlValidationRoute`` below converts the resulting 422 into the fixed
# 400. The marker never reaches a caller.
_URL_REJECTED_MARKER = "__google_maps_url_rejected__"

INTERNAL_ERROR_DETAIL = "The request could not be completed."

NOT_FOUND_DETAIL = "Not found."

# The only upstream status codes a caller is allowed to observe. The service
# layer builds its error dicts from whatever it caught, so an unfiltered
# ``result["status_code"]`` lets the upstream failure mode pick our HTTP status
# -- another way for the caller to tell one cause from another.
_PASSTHROUGH_STATUS_CODES = frozenset({400, 404})


def upstream_error(result: dict[str, Any], fallback: str) -> HTTPException:
    """Build the HTTP error for a service call that reported failure.

    The service layer puts ``str(exc)`` into ``result["message"]``, so passing
    that straight into ``detail`` -- which every endpoint here used to do --
    hands the caller Playwright, Redis and DNS internals: container host names,
    file paths, ports, and a different sentence per cause. The message is
    logged and the caller gets ``fallback``, which is a constant chosen per
    endpoint and never varies with the reason.

    Args:
        result: The service's error dict.
        fallback: Constant, cause-independent detail for the response.

    Returns:
        The exception to raise.
    """
    logger.warning("Upstream failure (%s): %s", fallback, result.get("message"))
    status_code = result.get("status_code")
    if status_code not in _PASSTHROUGH_STATUS_CODES:
        status_code = 500
    detail = NOT_FOUND_DETAIL if status_code == 404 else fallback
    return HTTPException(status_code=status_code, detail=detail)


def places_from_result(result: dict[str, Any], context: str) -> list[dict[str, Any]]:
    """Extract the place list from a service result, or fail loudly.

    The previous code did ``places = [] if not isinstance(raw, list)``, which
    turned a malformed or partially-failed upstream payload into a 200 with
    ``"success": true`` and zero results -- indistinguishable, to the caller,
    from a search that genuinely found nothing. A shape we do not recognise is
    a bug or an upstream failure, so it is logged and returned as a 500.

    Args:
        result: The raw service response.
        context: Short description used in the log line.

    Returns:
        Processed place dictionaries.

    Raises:
        HTTPException: 500, if the payload is not a list.
    """
    raw_places = result.get("results") or result.get("data") or result.get("places") or []
    if not isinstance(raw_places, list):
        logger.error(
            "%s: expected a list of places, got %s", scrub(context), type(raw_places).__name__
        )
        raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL)
    return google_maps_service.process_place_data(raw_places)


def validate_maps_url(value: str | None) -> str | None:
    """Return ``value`` normalised, or raise if it is not a fetchable Maps URL.

    Shared by every request model with a caller-supplied URL that ends up in
    Playwright's ``page.goto()``. Only the generic marker is raised:
    ``UrlNotAllowed.reason`` names the host and the precise cause and goes to
    the log alone, because returning it would tell the caller whether an
    internal name resolves and whether a port answers.

    Args:
        value: The untrusted URL, or None.

    Returns:
        The normalised URL, or ``value`` unchanged when it is empty.

    Known residual risk, not fixable from this layer:

    This validates the URL the caller supplied and nothing after it. Chromium
    then follows redirects on its own, and ``MAPS_ALLOWED_HOSTS`` includes
    ``goo.gl`` and ``maps.app.goo.gl`` -- shorteners -- as well as
    ``www.google.com``, which has historically carried open redirects. Any
    working redirector on an allow-listed host therefore reaches whatever it
    points at. ``ValidatedUrl.ip_addresses`` exists so the caller can pin the
    connection and close the same window against DNS rebinding, and is
    discarded here because the service layer does not yet accept it.

    Closing this properly means enforcing the allow-list at navigation time --
    a Playwright route handler that re-checks every request the page makes,
    pinned to the validated addresses -- which belongs in the scraper, not
    here. Until then treat the allow-list as reducing the attack surface, not
    as a complete barrier.

    Raises:
        ValueError: carrying ``_URL_REJECTED_MARKER`` and nothing else.
    """
    if value is None or not str(value).strip():
        return value
    try:
        validated = validate_outbound_url(value, allowed_hosts=MAPS_ALLOWED_HOSTS)
    except UrlNotAllowed as exc:
        logger.warning("Rejected caller-supplied Maps URL: %s", exc.reason)
        raise ValueError(_URL_REJECTED_MARKER) from None
    return validated.url


class SafeUrlValidationRoute(APIRoute):
    """Collapse caller-supplied-URL rejections into one fixed response.

    FastAPI's default 422 body echoes the offending ``input`` back and varies
    its ``msg`` per failure. Both are unacceptable for a URL the caller chose:
    the echo repeats the target host and the varying message is an oracle.
    This route class intercepts only validation errors that carry
    ``_URL_REJECTED_MARKER`` and answers them with a constant 400. Every other
    validation error is re-raised untouched, so ordinary request validation
    keeps its normal, useful 422.
    """

    def get_route_handler(self) -> Callable:
        original_route_handler = super().get_route_handler()

        async def safe_route_handler(request: Request) -> Response:
            try:
                return await original_route_handler(request)
            except RequestValidationError as exc:
                if any(
                    _URL_REJECTED_MARKER in str(error.get("msg", ""))
                    for error in exc.errors()
                ):
                    return JSONResponse(
                        status_code=400, content={"detail": URL_REJECTED_DETAIL}
                    )
                raise

        return safe_route_handler
