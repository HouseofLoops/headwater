"""
Google Autocomplete API integration for keyword generation.
Generates comprehensive keyword variations using Google Autocomplete suggestions.

This module provides a FastAPI endpoint for accessing Google's Autocomplete API
with support for all available parameters and output formats.
"""

import json
import logging
import time

# Parsing Google's autocomplete XML with the stdlib is safe on CPython 3.14:
# the bundled expat (>= 2.4.1) blocks entity-expansion attacks, and
# ElementTree never resolves external entities. See the "XML vulnerabilities"
# table in the Python docs.
import xml.etree.ElementTree as ET  # nosec B405
from datetime import datetime
from typing import Any, ClassVar

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from app.core.auth import get_api_key
from app.core.cache_manager import generate_cache_key, get_cached_or_fetch
from app.core.config import get_settings
from app.core.http_client import get_http_client_manager
from app.core.input_sanitizer import get_input_sanitizer
from app.core.proxy import get_proxy, mask_proxy
from app.core.rate_limiter import rate_limit
from app.schemas.enums import (
    ClientType,
    DataSource,
    OutputFormat,
    SafeSearch,
    SearchClient,
)
from app.services.google_autocomplete_service import google_autocomplete_service

# Configure logging
logger = logging.getLogger("uvicorn")
logging.basicConfig(level=logging.INFO)


# Pydantic model for request validation
class GoogleAutocompleteParams(BaseModel):
    """
    Pydantic model for Google Autocomplete API parameters.

    This model validates and documents all available parameters for the Google
    Autocomplete API based on the comprehensive reference guide.
    """

    # Core Parameters
    q: str = Field(..., description="Search query string (URL encoded)")
    output: OutputFormat = Field(OutputFormat.TOOLBAR, description="Response format (toolbar, firefox, chrome, etc.)")
    client: ClientType | None = Field(None, description="Client identifier (firefox, chrome, safari, opera)")

    # Geographic & Language Parameters
    gl: str = Field("US", description="Geographic location (country) using ISO country codes")
    hl: str = Field("en", description="Host language using ISO language codes")
    cr: str | None = Field(None, description="Country restrict (e.g., countryUS, countryUK)")

    # Data Source Parameters
    ds: DataSource | None = Field(None, description="Data source for suggestions (yt, i, n, s, v, b, p, etc.)")

    # Search Enhancement Parameters
    spell: int | None = Field(1, description="Enable spell correction (0=disabled, 1=enabled)")
    cp: int | None = Field(None, description="Cursor position in query (character position)")
    gs_rn: int | None = Field(None, description="Request number for sequential numbering")
    gs_id: str | None = Field(None, description="Session ID for tracking")

    # Response Parameters
    callback: str | None = Field(None, description="JSONP callback function name")
    jsonp: str | None = Field(None, description="JSONP wrapper (alternative to callback)")

    # Advanced Parameters
    psi: int | None = Field(None, description="Personalized search (0=disabled, 1=enabled)")
    pq: str | None = Field(None, description="Previous query for query refinement")
    complete: int | None = Field(None, description="Completion type affecting completion logic")
    suggid: str | None = Field(None, description="Suggestion ID for internal tracking")
    gs_l: str | None = Field(None, description="Google search location (internal parameter)")

    @field_validator("q")
    @classmethod
    def query_must_not_be_empty(cls, v: str) -> str:
        """Validate that query is not empty or whitespace-only."""
        if not v or not v.strip():
            raise ValueError("Query cannot be empty")
        return v.strip()

    class Config:
        """Configuration for the Pydantic model."""

        json_schema_extra: ClassVar[dict[str, Any]] = {
            "example": {
                "q": "chrome",
                "output": "chrome",
                "client": "chrome",
                "gl": "US",
                "hl": "en",
                "ds": "yt",
                "spell": 1,
            }
        }


# Create router with a specific tag to avoid duplication in documentation
router = APIRouter(tags=["Google Autocomplete API"])


@router.get(
    "/autocomplete",
    summary="Get Google Autocomplete suggestions",
    response_description="Autocomplete suggestions from Google",
    responses={
        200: {"description": "Autocomplete suggestions"},
        400: {"description": "Invalid parameters"},
        401: {"description": "Invalid API key"},
        422: {"description": "Validation error"},
        500: {"description": "Server error"},
    },
)
async def get_autocomplete(
    # === ESSENTIAL (Required/Primary) ===
    q: str = Query(..., min_length=1, description="Search query", examples=["python"]),
    output: OutputFormat = Query(
        OutputFormat.TOOLBAR, description="Response format (chrome/firefox=JSON with metadata, toolbar/xml=basic XML)"
    ),
    gl: str = Query("US", description="Country code (ISO)", examples=["US"]),
    hl: str = Query("en", description="Language code (ISO)", examples=["en"]),
    ds: DataSource | None = Query(
        None, description="Data source: yt=YouTube, i=Images, n=News, s=Shopping, b=Books, fin=Finance"
    ),
    variations: bool | None = Query(False, description="Return keyword variations instead of raw suggestions"),
    # === COMMONLY USED ===
    client: ClientType | None = Query(None, description="Client type (chrome, firefox, safari, opera)"),
    safe: SafeSearch | None = Query(None, description="SafeSearch filtering (active=filter, off=show all)"),
    spell: int | None = Query(None, description="Spell correction (0=off, 1=on)"),
    # === GEOGRAPHIC/LANGUAGE ===
    cr: str | None = Query(None, description="Country restrict (e.g., countryUS)"),
    lr: str | None = Query(None, description="Language restrict (e.g., lang_en)"),
    # === PERSONALIZATION ===
    psi: int | None = Query(None, description="Personalized search (0=off, 1=on)"),
    pws: int | None = Query(None, description="Personalized web search (0=off, 1=on)"),
    authuser: int | None = Query(None, description="Google account index (0, 1, 2...)"),
    # === CONTENT FILTERING ===
    nfpr: int | None = Query(None, description="Disable auto-correct (0=on, 1=off)"),
    filter: int | None = Query(None, description="Filter duplicates (0=off, 1=on)"),
    # === RESPONSE FORMAT ===
    callback: str | None = Query(None, description="JSONP callback function name"),
    jsonp: str | None = Query(None, description="JSONP wrapper (alt to callback)"),
    xssi: str | None = Query(None, description="XSSI protection (t=on, f=off)"),
    # === ENCODING ===
    ie: str | None = Query("UTF-8", description="Input encoding"),
    oe: str | None = Query("UTF-8", description="Output encoding"),
    # === ADVANCED/ANALYTICS (rarely needed) ===
    pq: str | None = Query(None, description="Previous query for refinement"),
    cp: int | None = Query(None, description="Cursor position in query"),
    complete: int | None = Query(None, description="Completion type"),
    oq: str | None = Query(None, description="Original typed query"),
    sclient: SearchClient | None = Query(None, description="Search client ID"),
    aqs: str | None = Query(None, description="Assisted query stats"),
    gs_rn: int | None = Query(None, description="Request sequence number"),
    gs_id: str | None = Query(None, description="Session ID"),
    suggid: str | None = Query(None, description="Suggestion tracking ID"),
    gs_l: str | None = Query(None, description="Google location codes"),
    # === AUTH ===
    api_key: str = Depends(get_api_key),
    rate_limit_check: None = Depends(rate_limit),
):
    """
    Get Google Autocomplete suggestions.

    Returns raw suggestions by default, or keyword variations when `variations=true`.

    **Data Sources (ds):** yt (YouTube), i (Images), n (News), s (Shopping),
    b (Books), fin (Finance), recipe, scholar, maps, flights, hotels

    **Output Formats:** toolbar/xml (XML), chrome/firefox/safari/opera (JSON)
    """
    # Validate query is not empty or whitespace-only
    q = q.strip()
    if not q:
        raise HTTPException(
            status_code=422,
            detail=[
                {
                    "type": "value_error",
                    "loc": ["query", "q"],
                    "msg": "Query cannot be empty or contain only whitespace",
                    "input": q,
                }
            ],
        )

    try:
        # Get proxy configuration
        proxy_url = await get_proxy()

        # Get HTTP client manager and input sanitizer
        http_manager = get_http_client_manager()
        sanitizer = get_input_sanitizer()

        # Start timing for response metadata
        start_time = time.time()

        # Sanitize and validate all input parameters
        if sanitizer.settings.INPUT_SANITIZATION_ENABLED:
            validation_result = sanitizer.validate_all_params(
                q=q,
                gl=gl,
                hl=hl,
                cr=cr,
                ds=ds,
                spell=spell,
                cp=cp,
                gs_rn=gs_rn,
                gs_id=gs_id,
                callback=callback,
                jsonp=jsonp,
                psi=psi,
                pq=pq,
                complete=complete,
                suggid=suggid,
                gs_l=gs_l,
            )

            if not validation_result["valid"]:
                logger.warning("Input validation failed: %s", validation_result["errors"])
                # Use sanitized values where possible
                if "query" in validation_result["results"]:
                    q = validation_result["results"]["query"]["sanitized"]
                if "country_code" in validation_result["results"]:
                    gl = validation_result["results"]["country_code"]["sanitized"]
                if "language_code" in validation_result["results"]:
                    hl = validation_result["results"]["language_code"]["sanitized"]

        # Set up HTTP client with proxy if available
        if proxy_url:
            logger.debug("Using proxy from environment settings: %s", mask_proxy(proxy_url))
        else:
            logger.debug("No proxy configured in environment. Proceeding without proxy.")

        # If variations is True, use the variations endpoint functionality
        if variations:
            logger.info(f"Generating keyword variations for query: {q}")

            # Get settings for parallel processing configuration
            settings = get_settings()
            max_parallel = settings.AUTOCOMPLETE_MAX_PARALLEL_REQUESTS

            # Build base params using service
            base_params = google_autocomplete_service.build_request_params(
                query=q,
                output=output.value,
                gl=gl,
                hl=hl,
                client=client.value if client else None,
                ds=ds.value if ds else None,
                spell=spell,
                cr=cr,
                cp=cp,
                gs_rn=gs_rn,
                gs_id=gs_id,
                callback=callback,
                jsonp=jsonp,
                psi=psi,
                pq=pq,
                complete=complete,
                suggid=suggid,
                gs_l=gs_l,
            )

            # Use HTTP client manager and service for parallel processing
            http_client = await http_manager.get_client(proxy_url)
            keyword_data = await google_autocomplete_service.generate_keyword_variations_parallel(
                http_client=http_client, base_query=q, params=base_params, max_parallel=max_parallel
            )

            # Calculate response time and add metadata
            end_time = time.time()
            response_time = end_time - start_time

            result = {
                "success": True,
                "message": "Success! Keywords Generated",
                "keyword_data": keyword_data,
                "response_metadata": {
                    "response_time_seconds": response_time,
                    "timestamp": datetime.now().isoformat(),
                    "request_count": http_manager.get_request_count(),
                    "connection_pool_stats": http_manager.get_connection_stats(),
                },
            }
            return result

        # Standard autocomplete functionality
        # Generate cache key
        cache_key = generate_cache_key(
            "autocomplete:suggestions",
            q=q,
            output=output.value,
            client=client.value if client else None,
            gl=gl,
            hl=hl,
            cr=cr,
            ds=ds,
            spell=spell,
            cp=cp,
            gs_rn=gs_rn,
            gs_id=gs_id,
            callback=callback,
            jsonp=jsonp,
            psi=psi,
            pq=pq,
            complete=complete,
            suggid=suggid,
            gs_l=gs_l,
        )

        async def fetch_autocomplete_suggestions():
            # Build URL with all provided parameters
            params = {"q": q, "output": output.value, "gl": gl, "hl": hl, "spell": spell}

            # Add optional parameters if provided
            if client:
                params["client"] = client.value
            if cr:
                params["cr"] = cr
            if ds:
                params["ds"] = ds
            if cp is not None:
                params["cp"] = cp
            if gs_rn is not None:
                params["gs_rn"] = gs_rn
            if gs_id:
                params["gs_id"] = gs_id
            if callback:
                params["callback"] = callback
            if jsonp:
                params["jsonp"] = jsonp
            if psi is not None:
                params["psi"] = psi
            if pq:
                params["pq"] = pq
            if complete is not None:
                params["complete"] = complete
            if suggid:
                params["suggid"] = suggid
            if gs_l:
                params["gs_l"] = gs_l
            # NEW: Safety & Content Filtering Parameters
            if safe:
                params["safe"] = safe.value
            if nfpr is not None:
                params["nfpr"] = nfpr
            if filter is not None:
                params["filter"] = filter
            # NEW: Encoding Parameters
            if ie:
                params["ie"] = ie
            if oe:
                params["oe"] = oe
            # NEW: Personalization Control Parameters
            if pws is not None:
                params["pws"] = pws
            if authuser is not None:
                params["authuser"] = authuser
            # NEW: Language Restriction Parameter
            if lr:
                params["lr"] = lr
            # NEW: Browser/Client Analytics Parameters
            if oq:
                params["oq"] = oq
            if sclient:
                params["sclient"] = sclient.value
            if aqs:
                params["aqs"] = aqs
            if xssi:
                params["xssi"] = xssi

            # Make request using HTTP client manager
            http_client = await http_manager.get_client(proxy_url)
            response = await http_client.get("https://www.google.com/complete/search", params=params)

            if response.status_code != 200:
                logger.error(f"Failed to retrieve suggestions. Status Code: {response.status_code}")
                raise HTTPException(status_code=response.status_code, detail="Failed to retrieve suggestions")

            # Calculate response time
            end_time = time.time()
            response_time = end_time - start_time

            # Determine the actual response format based on parameters and content
            # When client parameter is specified, Google usually returns JSON regardless of output parameter
            should_try_json_first = client is not None or output in [
                OutputFormat.CHROME,
                OutputFormat.FIREFOX,
                OutputFormat.SAFARI,
                OutputFormat.OPERA,
            ]

            # Log the first 100 characters of the response for debugging
            response_preview = response.text[:100] if response.text else "Empty response"
            logger.debug(f"Response preview: {response_preview}...")

            # Check if response looks like JSON (starts with [ or {) or JSONP (contains callback function)
            response_text = response.text.strip() if response.text else ""

            # Handle XSSI prefix stripping (Google prefixes responses with )]}\' when xssi=t)
            xssi_prefixes = [")]}'", ")]}'\n", ")]}'\r\n"]
            for prefix in xssi_prefixes:
                if response_text.startswith(prefix):
                    logger.debug(f"Stripping XSSI prefix: {prefix!r}")
                    response_text = response_text[len(prefix) :].strip()
                    break

            # Handle Google's internal callback format (window.google.ac.h(...))
            # This is triggered by sclient parameter and similar internal params
            google_callback_pattern = response_text.startswith("window.google.ac.h(") and response_text.endswith(")")

            looks_like_json = response_text.startswith(("[", "{"))
            looks_like_jsonp = (
                (callback or jsonp) and "(" in response_text and response_text.endswith(")")
            ) or google_callback_pattern

            # Smart response parsing - try the most likely format first, then fall back
            if should_try_json_first or looks_like_json or looks_like_jsonp:
                # Try JSON parsing first
                try:
                    # Handle JSONP response (callback wrapped JSON)
                    # This includes explicit callback/jsonp params AND Google's internal window.google.ac.h format
                    if looks_like_jsonp:
                        logger.debug("Detected JSONP/callback response, extracting JSON data")
                        # Extract JSON data from JSONP wrapper
                        # Format is typically: callback_name({"data": "value"}); or window.google.ac.h([[...]])
                        callback_name = response_text[: response_text.find("(")]
                        start_idx = response_text.find("(")
                        end_idx = response_text.rfind(")")

                        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                            json_str = response_text[start_idx + 1 : end_idx]
                            data = json.loads(json_str)

                            # Parse the Google Autocomplete response structure
                            # Format is typically: [query, [suggestions], [descriptions], [query_completions], {metadata}]
                            parsed_response = {
                                "callback": callback_name,
                                "response_type": "jsonp",
                                "original_query": data[0] if len(data) > 0 else "",
                                "suggestions": data[1] if len(data) > 1 else [],
                                "descriptions": data[2] if len(data) > 2 else [],
                                "query_completions": data[3] if len(data) > 3 else [],
                                "metadata": data[4] if len(data) > 4 else {},
                                "raw_response": data,
                                "response_metadata": {
                                    "response_time_seconds": response_time,
                                    "timestamp": datetime.now().isoformat(),
                                    "request_count": http_manager.get_request_count(),
                                    "connection_pool_stats": http_manager.get_connection_stats(),
                                },
                            }

                            # Log detailed information about the response
                            logger.debug(
                                f"Parsed JSONP response: query='{parsed_response['original_query']}', "
                                + f"suggestions_count={len(parsed_response['suggestions'])}, "
                                + f"metadata={json.dumps(parsed_response['metadata'])}"
                            )

                            return parsed_response
                        else:
                            logger.warning("Failed to extract JSON from JSONP response")
                            return {"raw_response": response_text, "response_type": "raw_jsonp"}
                    else:
                        # Regular JSON response
                        data = response.json()

                        # Check if this is a Google Autocomplete array format
                        if isinstance(data, list) and len(data) >= 2:
                            parsed_response = {
                                "response_type": "json",
                                "original_query": data[0] if len(data) > 0 else "",
                                "suggestions": data[1] if len(data) > 1 else [],
                                "descriptions": data[2] if len(data) > 2 else [],
                                "query_completions": data[3] if len(data) > 3 else [],
                                "metadata": data[4] if len(data) > 4 else {},
                                "raw_response": data,
                                "response_metadata": {
                                    "response_time_seconds": response_time,
                                    "timestamp": datetime.now().isoformat(),
                                    "request_count": http_manager.get_request_count(),
                                    "connection_pool_stats": http_manager.get_connection_stats(),
                                },
                            }
                            return parsed_response
                        else:
                            # Unknown JSON format, return as is
                            return {"raw_response": data, "response_type": "json"}
                except ValueError as e:
                    logger.warning(f"JSON parsing failed, falling back to XML: {e!s}")
                    # If client is specified but JSON parsing failed, log a warning about parameter conflict
                    if client is not None:
                        logger.warning(
                            f"Parameter conflict: client={client.value} specified but response is not valid JSON"
                        )

                    # If callback or jsonp is specified, the response might be a malformed JSONP
                    if callback or jsonp:
                        logger.warning(
                            f"JSONP parameters specified but couldn't parse response: callback={callback}, jsonp={jsonp}"
                        )
                        # Return the raw response for debugging
                        return {"raw_response": response_text, "response_type": "unparseable_jsonp"}

                    # Only fall back to XML if output is XML/toolbar
                    if output in [OutputFormat.XML, OutputFormat.TOOLBAR]:
                        try:
                            # Use response_text (with XSSI prefix stripped) instead of raw response.content
                            root = ET.fromstring(response_text.encode("utf-8"))  # nosec B314
                            suggestions = []
                            for complete_suggestion in root.findall("CompleteSuggestion"):
                                suggestion_element = complete_suggestion.find("suggestion")
                                if suggestion_element is not None:
                                    data = suggestion_element.get("data", "")
                                    suggestions.append(data)
                            return {"suggestions": suggestions}
                        except ET.ParseError as e:
                            logger.error(f"XML Parse Error: {e!s}")
                            logger.error(f"Response content: {response_text[:500]}...")
                            raise HTTPException(
                                status_code=500,
                                detail=f"Failed to parse response as XML or JSON. Parameter conflict may exist between output={output.value} and client={client.value if client else 'None'}",
                            ) from e
                    else:
                        # Both parsers failed: the upstream payload is not
                        # something we can interpret. Returning it as a 200
                        # with a raw_response field made an upstream format
                        # change look like a successful call to every client.
                        logger.error(
                            "Both JSON and XML parsing failed for autocomplete; first 500 chars: %s",
                            response_text[:500],
                        )
                        raise HTTPException(
                            status_code=502,
                            detail="Upstream returned a response we could not parse.",
                        ) from e
            else:
                # Try XML parsing first for toolbar/XML output formats
                try:
                    # Use response_text (with XSSI prefix stripped) instead of raw response.content
                    root = ET.fromstring(response_text.encode("utf-8"))  # nosec B314
                    suggestions = []
                    for complete_suggestion in root.findall("CompleteSuggestion"):
                        suggestion_element = complete_suggestion.find("suggestion")
                        if suggestion_element is not None:
                            data = suggestion_element.get("data", "")
                            suggestions.append(data)
                    return {"suggestions": suggestions}
                except ET.ParseError as e:
                    logger.warning(f"XML parsing failed, trying JSON: {e!s}")

                    # Fall back to JSON parsing - try to parse response_text as JSON
                    try:
                        data = json.loads(response_text)
                        return {"raw_response": data}
                    except ValueError as e2:
                        logger.error(f"Both XML and JSON parsing failed: {e2!s}")
                        logger.error(f"Response content: {response_text[:500]}...")
                        raise HTTPException(
                            status_code=500, detail="Failed to parse response as either XML or JSON"
                        ) from e2

        # Get cached result or fetch and cache
        return await get_cached_or_fetch(cache_key, fetch_autocomplete_suggestions)

    except Exception as e:
        logger.error(f"Error in get_autocomplete: {e!s}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {e!s}") from e
