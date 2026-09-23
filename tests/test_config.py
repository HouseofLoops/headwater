"""
Test suite for config.py module.

This module contains comprehensive tests for application configuration,
settings loading, validation, and caching functionality.
"""

import math
import os
from unittest.mock import patch

from app.core.config import (
    # Classes
    Settings,
    # Functions
    get_settings,
    # Global variables
    settings,
)


class TestSettingsDefaults:
    """Test Settings class with default values."""

    def test_settings_default_values(self, monkeypatch):
        """
        Test that Settings has correct default values.

        This must be isolated from BOTH ambient sources:
        - ``_env_file=None`` -- several assertions here previously encoded
          values that only existed in one developer's local .env ("From .env
          file"), so the test passed on that machine and nowhere else.
        - a scrubbed os.environ -- other test modules set variables such as
          ENVIRONMENT=testing without cleaning up, which made this test's
          result depend on collection order.
        """
        from app.__version__ import __version__ as expected_version

        for field_name in Settings.model_fields:
            monkeypatch.delenv(field_name, raising=False)
            monkeypatch.delenv(field_name.lower(), raising=False)

        test_settings = Settings(_env_file=None)

        # API settings
        assert test_settings.API_KEYS == []
        assert test_settings.API_KEY is None
        assert test_settings.ENABLE_API_KEY_AUTH is True

        # Rate limiting
        assert test_settings.RATE_LIMIT_ENABLED is True
        assert test_settings.RATE_LIMIT_REQUESTS == 100
        assert test_settings.RATE_LIMIT_TIMEFRAME == 3600

        # Caching
        assert test_settings.ENABLE_CACHE is True
        assert test_settings.CACHE_TTL == 3600
        assert test_settings.REDIS_URL is None

        # Proxy settings
        assert test_settings.ENABLE_PROXY is False
        assert test_settings.PROXY_URL is None
        assert test_settings.PROXY_URLS is None

        # CORS settings
        assert test_settings.CORS_ORIGINS == ["*"]
        assert test_settings.CORS_METHODS == ["*"]
        assert test_settings.CORS_HEADERS == ["*"]

        # Application settings
        assert test_settings.DEBUG is False
        assert test_settings.ENVIRONMENT == "development"
        assert test_settings.PROJECT_NAME == "Headwater"
        assert test_settings.VERSION == expected_version

    def test_settings_autocomplete_defaults(self):
        """Test autocomplete-related default values."""
        test_settings = Settings()

        assert test_settings.AUTOCOMPLETE_MAX_PARALLEL_REQUESTS == 10
        assert test_settings.AUTOCOMPLETE_REQUEST_TIMEOUT == 30
        assert test_settings.AUTOCOMPLETE_MAX_RETRIES == 3
        assert math.isclose(test_settings.AUTOCOMPLETE_RETRY_DELAY, 1.0)

    def test_settings_connection_pooling_defaults(self):
        """Test connection pooling default values."""
        test_settings = Settings()

        assert test_settings.HTTP_CONNECTION_POOL_SIZE == 20
        assert test_settings.HTTP_MAX_KEEPALIVE_CONNECTIONS == 10
        assert test_settings.HTTP_MAX_CONNECTIONS_PER_HOST == 5
        assert math.isclose(test_settings.HTTP_CONNECTION_TIMEOUT, 10.0)
        assert math.isclose(test_settings.HTTP_READ_TIMEOUT, 30.0)

    def test_settings_batch_processing_defaults(self):
        """Test batch processing default values."""
        test_settings = Settings()

        assert test_settings.BATCH_PROCESSING_ENABLED is True
        assert test_settings.BATCH_SIZE == 50
        assert math.isclose(test_settings.BATCH_TIMEOUT, 60.0)
        assert test_settings.MAX_CONCURRENT_BATCHES == 3

    def test_settings_input_sanitization_defaults(self):
        """Test input sanitization default values."""
        test_settings = Settings()

        assert test_settings.INPUT_SANITIZATION_ENABLED is True
        assert test_settings.MAX_QUERY_LENGTH == 200
        assert test_settings.ALLOWED_CHARACTERS_PATTERN == r"^[a-zA-Z0-9\s\-\.\,\?\!\(\)\[\]\{\}\'\"]+$"
        assert test_settings.BLOCK_SUSPICIOUS_PATTERNS is True
        assert len(test_settings.SUSPICIOUS_PATTERNS) > 0

    def test_settings_response_metadata_defaults(self):
        """Test response metadata default values."""
        test_settings = Settings()

        assert test_settings.RESPONSE_METADATA_ENABLED is True
        assert test_settings.INCLUDE_REQUEST_TIMING is True
        assert test_settings.INCLUDE_CONNECTION_INFO is True
        assert test_settings.INCLUDE_CACHE_INFO is True
        assert test_settings.INCLUDE_RATE_LIMIT_INFO is True


class TestSettingsEnvironmentLoading:
    """Test Settings loading from environment variables."""

    @patch.dict(
        os.environ,
        {
            "API_KEYS": '["key1","key2","key3"]',
            "ENABLE_API_KEY_AUTH": "false",
            "RATE_LIMIT_REQUESTS": "50",
            "DEBUG": "true",
            "ENVIRONMENT": "production",
            # A production environment must supply a real secret: Settings now
            # refuses to boot outside development while a credential still holds
            # the placeholder value published in .env.example / config.py.
            "SECRET_KEY": "a-real-generated-production-secret-key-value",
        },
    )
    def test_settings_from_env_basic(self):
        """Test loading basic settings from environment."""
        test_settings = Settings()

        assert test_settings.API_KEYS == ["key1", "key2", "key3"]
        assert test_settings.ENABLE_API_KEY_AUTH is False
        assert test_settings.RATE_LIMIT_REQUESTS == 50
        assert test_settings.DEBUG is True
        assert test_settings.ENVIRONMENT == "production"

    @patch.dict(os.environ, {"REDIS_URL": "redis://localhost:6379", "PROXY_URL": "http://proxy.example.com:8080"})
    def test_settings_from_env_urls(self):
        """Test loading URL settings from environment."""
        test_settings = Settings()

        assert str(test_settings.REDIS_URL) == "redis://localhost:6379/0"
        assert test_settings.PROXY_URL == "http://proxy.example.com:8080"

    @patch.dict(
        os.environ,
        {
            "CORS_ORIGINS": '["https://example.com","https://app.example.com"]',
            "CORS_METHODS": '["GET","POST","PUT"]',
            "CORS_HEADERS": '["Content-Type","Authorization"]',
        },
    )
    def test_settings_from_env_cors(self):
        """Test loading CORS settings from environment."""
        test_settings = Settings()

        assert test_settings.CORS_ORIGINS == ["https://example.com", "https://app.example.com"]
        assert test_settings.CORS_METHODS == ["GET", "POST", "PUT"]
        assert test_settings.CORS_HEADERS == ["Content-Type", "Authorization"]

    @patch.dict(os.environ, {"SECRET_KEY": "production-secret-key", "X_BEARER_TOKEN": "bearer-token-123"})
    def test_settings_from_env_security(self):
        """Test loading security settings from environment."""
        test_settings = Settings()

        assert test_settings.SECRET_KEY == "production-secret-key"
        assert test_settings.X_BEARER_TOKEN == "bearer-token-123"


class TestFieldValidators:
    """Test field validators in Settings class."""

    def test_api_keys_validator_string_input(self):
        """Test API_KEYS validator with string input."""
        # Test comma-separated string
        result = Settings.assemble_list_setting("key1,key2,key3")
        assert result == ["key1", "key2", "key3"]

    def test_api_keys_validator_whitespace_handling(self):
        """Test API_KEYS validator handles whitespace."""
        result = Settings.assemble_list_setting("  key1  ,  key2,key3  ")
        assert result == ["key1", "key2", "key3"]

    def test_api_keys_validator_empty_values(self):
        """Test API_KEYS validator handles empty values."""
        result = Settings.assemble_list_setting("key1,,key2,")
        assert result == ["key1", "key2"]

    def test_api_keys_validator_list_input(self):
        """Test API_KEYS validator with list input."""
        result = Settings.assemble_list_setting(["key1", "key2", "key3"])
        assert result == ["key1", "key2", "key3"]

    def test_api_keys_validator_empty_string(self):
        """Test API_KEYS validator with empty string."""
        result = Settings.assemble_list_setting("")
        assert result == []

    def test_api_keys_validator_none_input(self):
        """Test API_KEYS validator with None input."""
        result = Settings.assemble_list_setting(None)
        assert result == []

    def test_cors_origins_validator_string_input(self):
        """Test CORS_ORIGINS validator with string input."""
        result = Settings.assemble_list_setting("https://example.com,https://app.example.com")
        assert result == ["https://example.com", "https://app.example.com"]

    def test_cors_origins_validator_whitespace_handling(self):
        """Test CORS_ORIGINS validator handles whitespace."""
        result = Settings.assemble_list_setting("  https://example.com  ,  https://app.example.com  ")
        assert result == ["https://example.com", "https://app.example.com"]

    def test_cors_origins_validator_list_input(self):
        """Test CORS_ORIGINS validator with list input."""
        result = Settings.assemble_list_setting(["https://example.com", "https://app.example.com"])
        assert result == ["https://example.com", "https://app.example.com"]

    def test_cors_origins_validator_empty_string(self):
        """Test CORS_ORIGINS validator with empty string."""
        result = Settings.assemble_list_setting("")
        assert result == []


class TestSettingsValidation:
    """Test Settings validation and edge cases."""

    def test_settings_case_insensitive_env(self):
        """Test that environment variables are case insensitive."""
        with patch.dict(
            os.environ,
            {
                "debug": "true",
                "environment": "production",
                "secret_key": "a-real-generated-production-secret-key-value",
            },
        ):
            test_settings = Settings()
            assert test_settings.DEBUG is True
            assert test_settings.ENVIRONMENT == "production"

    def test_settings_backward_compatibility(self):
        """Test backward compatibility settings."""
        with patch.dict(
            os.environ,
            {
                "API_KEY": "legacy_key",
                "PROXY_URLS": "http://proxy1.com,http://proxy2.com",
                "TWITTER_API_KEY": "twitter_key",
            },
        ):
            test_settings = Settings()
            assert test_settings.API_KEY == "legacy_key"
            assert test_settings.PROXY_URLS == "http://proxy1.com,http://proxy2.com"
            assert test_settings.TWITTER_API_KEY == "twitter_key"


class TestGetSettingsFunction:
    """Test the get_settings function."""

    def test_get_settings_returns_settings_instance(self):
        """Test that get_settings returns a Settings instance."""
        result = get_settings()
        assert isinstance(result, Settings)

    def test_get_settings_caching(self):
        """Test that get_settings is cached."""
        settings1 = get_settings()
        settings2 = get_settings()
        assert settings1 is settings2  # Same instance due to caching

    @patch.dict(os.environ, {"DEBUG": "true"})
    def test_get_settings_with_env_changes(self):
        """Test get_settings with environment changes."""
        # Clear cache to get fresh settings
        get_settings.cache_clear()
        result = get_settings()
        assert result.DEBUG is True


class TestGlobalSettings:
    """Test global settings instance."""

    def test_global_settings_instance(self):
        """Test that global settings is a Settings instance."""
        assert isinstance(settings, Settings)

    def test_global_settings_has_expected_attributes(self):
        """Test that global settings has expected attributes."""
        assert hasattr(settings, "API_KEYS")
        assert hasattr(settings, "DEBUG")
        assert hasattr(settings, "ENVIRONMENT")
        assert hasattr(settings, "PROJECT_NAME")

    def test_global_settings_default_values(self):
        """Test that global settings has correct default values."""
        assert settings.API_KEYS == []
        assert settings.ENABLE_API_KEY_AUTH is True
        assert settings.DEBUG is False


class TestSettingsIntegration:
    """Test Settings integration scenarios."""

    @patch.dict(
        os.environ,
        {
            "API_KEYS": '["prod_key1","prod_key2"]',
            "ENABLE_API_KEY_AUTH": "true",
            "RATE_LIMIT_REQUESTS": "200",
            "DEBUG": "false",
            "ENVIRONMENT": "production",
            "SECRET_KEY": "a-real-generated-production-secret-key-value",
            "REDIS_URL": "redis://prod-redis:6379",
        },
    )
    def test_production_settings_configuration(self):
        """Test complete production settings configuration."""
        test_settings = Settings()

        # API settings
        assert test_settings.API_KEYS == ["prod_key1", "prod_key2"]
        assert test_settings.ENABLE_API_KEY_AUTH is True

        # Rate limiting
        assert test_settings.RATE_LIMIT_REQUESTS == 200

        # Environment
        assert test_settings.DEBUG is False
        assert test_settings.ENVIRONMENT == "production"

        # URLs
        assert str(test_settings.REDIS_URL) == "redis://prod-redis:6379/0"

    def test_settings_immutability(self):
        """Test that settings instances are mutable after creation."""
        test_settings = Settings()
        original_debug = test_settings.DEBUG

        # Try to modify (this should work)
        test_settings.DEBUG = not original_debug

        # Verify it changed
        assert test_settings.DEBUG != original_debug
