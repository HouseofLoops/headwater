"""
Authentication utilities for API key validation.

This module provides functions and dependencies for validating API keys
in incoming requests.

Configuration comes from a single source: ``app.core.config.Settings``.
There used to be a second ``AuthSettings`` class here plus a raw
``os.getenv("API_KEY")`` lookup; between them the documented ``API_KEY``
variable was never actually loaded from ``.env`` (pydantic-settings reads the
file itself and does not export values into ``os.environ``), so every
authenticated request failed. Read keys from ``get_settings()`` only.
"""
import threading
from typing import NamedTuple

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security.api_key import APIKeyHeader

from app.core.config import Settings, get_settings


class _AuthState(NamedTuple):
    """
    One immutable snapshot of everything an auth decision depends on.

    Auth mode and the accepted key set must come from the SAME Settings
    instance. Kept as a single tuple, replaced by one atomic rebind, so a
    settings reload concurrent with an in-flight request can never make that
    request apply one config's ENABLE_API_KEY_AUTH against another config's
    keys.
    """

    settings: Settings | None
    keys: frozenset[str]
    metadata: dict[str, dict]

# Create API Key header schema.
#
# auto_error MUST be False. With auto_error=True FastAPI rejects a request that
# carries no X-API-Key header before this module's code runs at all -- which
# means the ENABLE_API_KEY_AUTH=false branch below could never be reached, and
# "auth disabled" inverted into "auth required, and any non-empty key passes".
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# The single source of truth for auth decisions. Replaced wholesale, never
# mutated in place.
_auth_state = _AuthState(settings=None, keys=frozenset(), metadata={})

# Serialises rebuilds so a slow thread cannot publish a snapshot it derived
# from settings that have since been replaced.
_refresh_lock = threading.Lock()


def initialize_api_keys(settings: Settings | None = None) -> set[str]:
    """
    (Re)build the set of accepted API keys from application settings.

    Keys are read from:
    1. ``API_KEYS`` -- comma-separated or JSON list.
    2. ``API_KEY`` -- a single key, the form documented in the README.

    Args:
        settings: Settings to read from. Defaults to ``get_settings()``.

    Returns:
        Set[str]: The set of accepted API keys.
    """
    global _auth_state

    settings = settings if settings is not None else get_settings()

    keys: set[str] = set()
    metadata: dict[str, dict] = {}

    candidates = list(settings.API_KEYS or [])
    if settings.API_KEY:
        candidates.append(settings.API_KEY)

    for key in candidates:
        if key and str(key).strip():
            stripped = str(key).strip()
            keys.add(stripped)
            metadata[stripped] = {"source": "settings"}

    # One atomic rebind: any concurrent reader sees either the whole old
    # snapshot or the whole new one, never a mix.
    _auth_state = _AuthState(
        settings=settings, keys=frozenset(keys), metadata=metadata
    )

    if not keys and settings.ENABLE_API_KEY_AUTH:
        print(
            "WARNING: No API keys configured. API key authentication is "
            "enabled but will reject all requests."
        )

    return set(keys)


def _auth_snapshot() -> _AuthState:
    """
    Return the current auth snapshot, rebuilding it if settings were reloaded.

    Every public entry point in this module goes through here, so
    ``reload_settings()`` (or any ``get_settings.cache_clear()``) takes effect
    immediately rather than only on the next authenticated request.

    The rebuild is serialised and re-checked under ``_refresh_lock``. Without
    that, a thread descheduled between reading ``get_settings()`` and
    publishing could overwrite a newer snapshot with the keys it read earlier
    -- reinstating credentials that a rotation had just removed.

    Returns:
        _AuthState: A consistent (settings, keys, metadata) triple.
    """
    settings = get_settings()
    state = _auth_state
    if state.settings is settings:
        return state

    with _refresh_lock:
        # Re-read inside the lock: another thread may have rebuilt already,
        # and get_settings() may have moved on since the check above.
        settings = get_settings()
        if _auth_state.settings is not settings:
            initialize_api_keys(settings)
        return _auth_state


# Initialize API keys on module import.
initialize_api_keys()


def validate_api_key(api_key: str | None) -> bool:
    """
    Validate if the provided API key is valid.

    Args:
        api_key: The API key to validate

    Returns:
        bool: True if the API key is valid, False otherwise
    """
    if not api_key:
        return False
    return api_key in _auth_snapshot().keys


def get_api_key_metadata(api_key: str) -> dict | None:
    """
    Get metadata for an API key.

    Args:
        api_key: The API key to get metadata for

    Returns:
        Optional[Dict]: Metadata for the API key, or None if the key is invalid
    """
    return _auth_snapshot().metadata.get(api_key)


async def get_api_key(api_key_header: str | None = Security(api_key_header)) -> str:
    """
    Validate API key from request header.

    This function is kept for backward compatibility.

    Args:
        api_key_header: The API key from the request header, or None if absent

    Returns:
        str: The validated API key

    Raises:
        HTTPException: If the API key is missing, invalid, or unconfigured
    """
    return await authenticate_api_key(api_key_header)


async def authenticate_api_key(
    api_key_header: str | None = Security(api_key_header),
    request: Request | None = None
) -> str:
    """
    Validate API key from request header.

    This function can be used as a dependency in FastAPI routes.

    Args:
        api_key_header: The API key from the request header, or None if the
            header was not sent
        request: Optional request object for future use (e.g., rate limiting)

    Returns:
        str: The validated API key

    Raises:
        HTTPException: 401 if the key is missing or invalid; 500 if key
            authentication is enabled but no keys are configured.
    """
    # Take ONE snapshot and decide entirely from it, so auth mode and the
    # accepted keys always come from the same configuration.
    state = _auth_snapshot()

    # Authentication explicitly disabled: allow the request through with no
    # header at all. Reachable only because api_key_header uses
    # auto_error=False.
    if not state.settings.ENABLE_API_KEY_AUTH:
        return "authentication-disabled"

    # No header sent (or sent empty) -> unauthenticated, not a server error.
    if not api_key_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Provide it in the X-API-Key header.",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    # Fail closed: auth is on but nothing was configured to accept.
    if not state.keys:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="API key authentication is enabled but no API keys are configured."
        )

    # Validate the API key
    if api_key_header not in state.keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key provided.",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    return api_key_header


def get_current_api_key(
    api_key: str = Depends(authenticate_api_key)
) -> str:
    """
    Get the current API key.

    This is a convenience dependency that can be used in routes
    that need access to the current API key.

    Args:
        api_key: The API key from authenticate_api_key

    Returns:
        str: The current API key
    """
    return api_key
