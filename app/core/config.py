"""
Configuration settings for the Headwater application.

This module provides a centralized way to access configuration settings
from environment variables using Pydantic's BaseSettings.
"""

import json
from functools import lru_cache
from typing import Annotated

from pydantic import (
    RedisDsn,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode

# Placeholder credentials shipped in .env.example. Usable in development so
# `cp .env.example .env` boots, rejected everywhere else -- otherwise a
# deployment that copies the file unchanged would run behind a credential
# published in this repository. Compared lowercase.
PLACEHOLDER_CREDENTIALS = frozenset(
    {
        "your-secure-api-key-here",
        "your-secure-secret-key-minimum-32-characters-here",
        "development-secret-key-change-in-production",
        "changeme",
        "change-me",
    }
)

# Environments where placeholder credentials are tolerated.
_NON_PRODUCTION_ENVIRONMENTS = frozenset({"development", "dev", "local", "test", "testing"})

# Fields that are parsed from a delimited string rather than JSON.
# ``NoDecode`` disables pydantic-settings' built-in JSON decoding for complex
# types so that our ``mode="before"`` validators actually receive the raw
# string from the environment / .env file. Without it pydantic-settings tries
# ``json.loads`` FIRST and raises ``SettingsError`` on ``API_KEYS=key1,key2``
# before any validator can run.
CsvList = Annotated[list[str], NoDecode]


def _parse_delimited_list(value: str | list[str] | None) -> list[str]:
    """
    Parse a list-valued setting from either a JSON array or a comma-separated
    string.

    Both of these are accepted for every list field::

        API_KEYS=key1,key2
        API_KEYS=["key1","key2"]

    Args:
        value: Raw value from the environment, .env file, or Python default.

    Returns:
        List[str]: The parsed, whitespace-stripped list.
    """
    if value is None:
        return []
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        # Accept the JSON array form that pydantic-settings used to require.
        if raw[0] == "[":
            try:
                decoded = json.loads(raw)
            except ValueError:
                decoded = None
            if isinstance(decoded, list):
                return [str(item).strip() for item in decoded if str(item).strip()]
        return [item.strip() for item in raw.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


# Import version from version file
try:
    from app.__version__ import __version__ as app_version
except ImportError:
    app_version = "0.1.0"  # Fallback version


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.

    This class uses Pydantic's BaseSettings to load and validate
    configuration settings from environment variables.
    """

    # API settings
    # Accepts "key1,key2" or '["key1","key2"]'. API_KEY (below) is merged in
    # by app.core.auth so the single-key form documented in the README works.
    API_KEYS: CsvList = []
    ENABLE_API_KEY_AUTH: bool = True

    # Rate limiting
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_REQUESTS: int = 100
    RATE_LIMIT_TIMEFRAME: int = 3600  # seconds

    # Caching
    ENABLE_CACHE: bool = True
    CACHE_TTL: int = 3600  # seconds
    REDIS_URL: RedisDsn | None = None

    # Proxy settings
    ENABLE_PROXY: bool = False
    PROXY_URL: str | None = None
    # Hosts that must never be proxied, comma separated. Suffix match, so
    # "youtube.com" also covers www. and m. See proxy.proxy_for().
    NO_PROXY_HOSTS: str | None = None

    # CORS settings
    CORS_ORIGINS: CsvList = ["*"]
    CORS_METHODS: CsvList = ["*"]
    CORS_HEADERS: CsvList = ["*"]

    # Autocomplete settings
    AUTOCOMPLETE_MAX_PARALLEL_REQUESTS: int = 10
    AUTOCOMPLETE_REQUEST_TIMEOUT: int = 30
    AUTOCOMPLETE_MAX_RETRIES: int = 3
    AUTOCOMPLETE_RETRY_DELAY: float = 1.0

    # Connection Pooling settings
    HTTP_CONNECTION_POOL_SIZE: int = 20
    HTTP_MAX_KEEPALIVE_CONNECTIONS: int = 10
    HTTP_MAX_CONNECTIONS_PER_HOST: int = 5
    HTTP_CONNECTION_TIMEOUT: float = 10.0
    HTTP_READ_TIMEOUT: float = 30.0

    # Batch Processing settings
    BATCH_PROCESSING_ENABLED: bool = True
    BATCH_SIZE: int = 50
    BATCH_TIMEOUT: float = 60.0
    MAX_CONCURRENT_BATCHES: int = 3

    # Input Sanitization settings
    INPUT_SANITIZATION_ENABLED: bool = True
    MAX_QUERY_LENGTH: int = 200
    ALLOWED_CHARACTERS_PATTERN: str = r"^[a-zA-Z0-9\s\-\.\,\?\!\(\)\[\]\{\}\'\"]+$"
    BLOCK_SUSPICIOUS_PATTERNS: bool = True
    SUSPICIOUS_PATTERNS: CsvList = ["<script", "javascript:", "onload=", "onerror=", "eval(", "alert("]

    # Response Metadata settings
    RESPONSE_METADATA_ENABLED: bool = True
    INCLUDE_REQUEST_TIMING: bool = True
    INCLUDE_CONNECTION_INFO: bool = True
    INCLUDE_CACHE_INFO: bool = True
    INCLUDE_RATE_LIMIT_INFO: bool = True

    # API Keys (for backward compatibility)
    API_KEY: str | None = None

    # Proxy settings (for backward compatibility)
    PROXY_URLS: str | None = None

    # Twitter API settings (for backward compatibility)
    TWITTER_API_KEY: str | None = None
    TWITTER_API_SECRET_KEY: str | None = None
    TWITTER_ACCESS_TOKEN: str | None = None
    TWITTER_ACCESS_TOKEN_SECRET: str | None = None
    TWITTER_BEARER_TOKEN: str | None = None

    # Security
    SECRET_KEY: str = "development-secret-key-change-in-production"
    X_BEARER_TOKEN: str | None = None

    # Application settings
    DEBUG: bool = False
    ENVIRONMENT: str = "development"
    PROJECT_NAME: str = "Headwater"
    VERSION: str = app_version  # Use version from __version__.py
    DESCRIPTION: str = "API for Google Search, News, Trends and Maps data, plus YouTube transcripts"

    @field_validator(
        "API_KEYS",
        "CORS_ORIGINS",
        "CORS_METHODS",
        "CORS_HEADERS",
        "SUSPICIOUS_PATTERNS",
        mode="before",
    )
    @classmethod
    def assemble_list_setting(cls, v: str | list[str] | None) -> list[str]:
        """
        Parse a list-valued setting from a comma-separated or JSON string.

        These fields are annotated with ``NoDecode`` so this validator is the
        ONLY parser for them; pydantic-settings no longer JSON-decodes the raw
        environment value ahead of us.

        Args:
            v: The raw value from the environment, .env file, or default.

        Returns:
            List[str]: The parsed list.
        """
        return _parse_delimited_list(v)

    @model_validator(mode="after")
    def reject_placeholder_credentials(self) -> Settings:
        """
        Refuse to run outside development with the .env.example placeholders.

        ``.env.example`` ships working-looking placeholder credentials so that
        `cp .env.example .env` boots. Without this guard, a deployment that
        copies the file and forgets to edit it would expose the API behind a
        credential printed in the public repository. Development and test
        environments are exempt so the documented quickstart still works.

        Returns:
            Settings: self, when the configuration is acceptable.

        Raises:
            ValueError: If a placeholder credential is in use outside a
                development or test environment.
        """
        if (self.ENVIRONMENT or "").strip().lower() in _NON_PRODUCTION_ENVIRONMENTS:
            return self

        offenders = []
        for key in self.API_KEYS:
            if key.strip().lower() in PLACEHOLDER_CREDENTIALS:
                offenders.append("API_KEYS")
                break
        if self.API_KEY and self.API_KEY.strip().lower() in PLACEHOLDER_CREDENTIALS:
            offenders.append("API_KEY")
        if self.SECRET_KEY.strip().lower() in PLACEHOLDER_CREDENTIALS:
            offenders.append("SECRET_KEY")

        if offenders:
            raise ValueError(
                f"{', '.join(offenders)} still holds the placeholder value "
                f"shipped in .env.example, and ENVIRONMENT is "
                f"'{self.ENVIRONMENT}'. Generate real secrets before running "
                "outside development."
            )
        return self

    model_config = {
        "env_file": ".env",
        "case_sensitive": False,
        "env_file_encoding": "utf-8",
        # Deployment .env files legitimately carry variables this app does not
        # own (REDIS_PASSWORD, DATAFORSEO_*, and
        # anything docker-compose interpolates). Without "ignore",
        # pydantic-settings defaults to "forbid" and the app cannot boot from
        # its own documented .env.example.
        "extra": "ignore",
    }


class SettingsError(RuntimeError):
    """
    Raised when settings cannot be loaded.

    This deliberately replaces pydantic's ``ValidationError``, whose message
    echoes the offending *values* -- which for a .env file means database
    passwords and API keys end up in stack traces, CI logs and crash reports.
    Only field names and error types are reported here.
    """


def _build_settings() -> Settings:
    """
    Construct Settings, converting validation failures into a redacted error.

    Returns:
        Settings: The loaded settings.

    Raises:
        SettingsError: If any setting fails validation. The message names the
            offending fields and the reason, but never their values.
    """
    try:
        return Settings()
    except ValidationError as exc:
        problems = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error.get("loc", ()))
            if location:
                # Field-level failure: name the field and the error type only.
                # The rejected value stays out -- that is the whole point.
                problems.append(f"{location} ({error.get('type', 'invalid')})")
            else:
                # Model-level (@model_validator) failure. The message is ours,
                # written not to contain any credential, so it is safe to show
                # and is the only thing identifying what went wrong.
                problems.append(error.get("msg", "invalid configuration"))
        raise SettingsError(
            "Invalid application configuration; "
            f"{len(problems)} setting(s) failed validation: {', '.join(problems)}. "
            "Values are omitted deliberately -- they may contain secrets."
        ) from None


@lru_cache
def get_settings() -> Settings:
    """
    Get the application settings.

    This function is cached using @lru_cache to avoid loading settings
    multiple times. The cache ensures that environment variables are only
    parsed once at startup, improving performance.

    Caching Behavior:
        - Settings are loaded once on first call
        - Subsequent calls return the cached instance
        - Use reload_settings() to force a refresh

    Returns:
        Settings: The application settings instance

    Example:
        >>> from app.core.config import get_settings
        >>> settings = get_settings()
        >>> print(settings.ENVIRONMENT)
        'development'

    Raises:
        SettingsError: If configuration is invalid. The error names the
            offending fields but never echoes their values.
    """
    return _build_settings()


def reload_settings() -> Settings:
    """
    Reload settings by clearing the cache and creating a new instance.

    This function clears the lru_cache and reloads settings from
    environment variables. Useful for runtime configuration updates
    or testing scenarios.

    Note:
        This function also updates the module-level 'settings' variable.
        Any code holding references to the old settings instance will
        not see the updated values - they should call get_settings() again.

    Returns:
        Settings: The newly loaded settings instance

    Example:
        >>> from app.core.config import reload_settings
        >>> import os
        >>> os.environ['DEBUG'] = 'true'
        >>> settings = reload_settings()
        >>> assert settings.DEBUG == True
    """
    global settings
    get_settings.cache_clear()
    settings = get_settings()
    return settings


def is_settings_cached() -> bool:
    """
    Check if settings are currently cached.

    Returns:
        bool: True if settings are cached, False otherwise

    Example:
        >>> from app.core.config import is_settings_cached, get_settings
        >>> get_settings.cache_clear()
        >>> assert is_settings_cached() == False
        >>> _ = get_settings()
        >>> assert is_settings_cached() == True
    """
    cache_info = get_settings.cache_info()
    return cache_info.hits > 0 or cache_info.currsize > 0


def get_settings_cache_info() -> dict:
    """
    Get cache statistics for the settings.

    Returns:
        dict: Cache info including hits, misses, maxsize, currsize

    Example:
        >>> from app.core.config import get_settings_cache_info
        >>> info = get_settings_cache_info()
        >>> print(f"Cache hits: {info['hits']}")
    """
    cache_info = get_settings.cache_info()
    return {
        "hits": cache_info.hits,
        "misses": cache_info.misses,
        "maxsize": cache_info.maxsize,
        "currsize": cache_info.currsize,
    }


# Global settings instance for direct imports
# This provides a convenient way to access settings without calling get_settings()
# Note: Use get_settings() in production code for better testability
settings = get_settings()
