from app.core.config import Settings


def test_config_values():
    """Test that configuration values are properly loaded."""
    settings = Settings()

    # Test default values
    assert hasattr(settings, 'AUTOCOMPLETE_MAX_PARALLEL_REQUESTS')
    assert hasattr(settings, 'AUTOCOMPLETE_REQUEST_TIMEOUT')
    assert hasattr(settings, 'AUTOCOMPLETE_MAX_RETRIES')
    assert hasattr(settings, 'AUTOCOMPLETE_RETRY_DELAY')

    # Test that values are reasonable
    assert settings.AUTOCOMPLETE_MAX_PARALLEL_REQUESTS > 0
    assert settings.AUTOCOMPLETE_REQUEST_TIMEOUT > 0
    assert settings.AUTOCOMPLETE_MAX_RETRIES >= 0
    assert settings.AUTOCOMPLETE_RETRY_DELAY >= 0


def test_env_file_values():
    """Test that environment variables override defaults."""
    import os

    # Set environment variables
    os.environ['AUTOCOMPLETE_MAX_PARALLEL_REQUESTS'] = '5'
    os.environ['AUTOCOMPLETE_REQUEST_TIMEOUT'] = '60'
    os.environ['AUTOCOMPLETE_MAX_RETRIES'] = '2'
    os.environ['AUTOCOMPLETE_RETRY_DELAY'] = '2.0'

    try:
        # Create new settings instance to pick up env vars
        settings = Settings()

        # Test that env vars are used
        assert settings.AUTOCOMPLETE_MAX_PARALLEL_REQUESTS == 5
        assert settings.AUTOCOMPLETE_REQUEST_TIMEOUT == 60
        assert settings.AUTOCOMPLETE_MAX_RETRIES == 2
        # Use approximate comparison for float
        assert abs(settings.AUTOCOMPLETE_RETRY_DELAY - 2.0) < 0.001

    finally:
        # Clean up environment variables
        del os.environ['AUTOCOMPLETE_MAX_PARALLEL_REQUESTS']
        del os.environ['AUTOCOMPLETE_REQUEST_TIMEOUT']
        del os.environ['AUTOCOMPLETE_MAX_RETRIES']
        del os.environ['AUTOCOMPLETE_RETRY_DELAY']
