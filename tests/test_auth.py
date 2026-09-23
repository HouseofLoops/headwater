"""
Test suite for auth.py module.

This module contains comprehensive tests for API key authentication,
validation, and FastAPI dependency injection functionality.

Note: the ``AuthSettings`` class these tests used to exercise has been
deleted. It was a second, divergent settings source that declared no
``API_KEY`` field at all, so the documented single-key variable was never
loaded and every authenticated request returned 500. Authentication now reads
``app.core.config.Settings`` -- the same object the rest of the app uses --
so these tests exercise that instead.
"""

import os
from unittest.mock import patch

import pytest

from app.core import auth as auth_module
from app.core.auth import (
    _auth_snapshot,
    # Objects
    api_key_header,
    authenticate_api_key,
    get_api_key_metadata,
    get_current_api_key,
    initialize_api_keys,
    # Functions
    validate_api_key,
)
from app.core.config import Settings, get_settings


class TestAuthSettingsSource:
    """Authentication configuration comes from Settings, and only Settings."""

    def test_settings_auth_defaults(self):
        """Settings supplies the auth defaults auth.py relies on."""
        settings = Settings(_env_file=None)
        assert settings.API_KEYS == []
        assert settings.API_KEY is None
        assert settings.ENABLE_API_KEY_AUTH is True

    @patch.dict(os.environ, {"API_KEYS": '["key1","key2","key3"]'})
    def test_settings_api_keys_json_form(self):
        """API keys load from the JSON list form."""
        settings = Settings(_env_file=None)
        assert settings.API_KEYS == ["key1", "key2", "key3"]

    @patch.dict(os.environ, {"API_KEYS": "key1,key2,key3"})
    def test_settings_api_keys_comma_form(self):
        """API keys load from the comma-separated form the README documents."""
        settings = Settings(_env_file=None)
        assert settings.API_KEYS == ["key1", "key2", "key3"]

    @patch.dict(os.environ, {"ENABLE_API_KEY_AUTH": "false"})
    def test_settings_disable_auth(self):
        """Authentication can be turned off via Settings."""
        settings = Settings(_env_file=None)
        assert settings.ENABLE_API_KEY_AUTH is False

    def test_no_separate_auth_settings_class(self):
        """
        There must be exactly one settings source.

        A second class here is how API_KEY silently stopped being loaded.
        """
        assert not hasattr(auth_module, "AuthSettings")

    @patch.dict(os.environ, {"API_KEY": "single-documented-key"})
    def test_single_api_key_variable_is_loaded(self):
        """
        The documented API_KEY variable must reach the accepted key set.

        This is the regression that made every authenticated request 500:
        auth.py read it with os.getenv, which never sees values that
        pydantic-settings read out of a .env file.
        """
        get_settings.cache_clear()
        try:
            keys = initialize_api_keys(get_settings())
            assert "single-documented-key" in keys
            assert validate_api_key("single-documented-key") is True
        finally:
            get_settings.cache_clear()
            initialize_api_keys(get_settings())


class TestAPIKeyValidation:
    """Test API key validation functions."""

    def setup_method(self):
        """Reset global state before each test."""
        self.original_state = auth_module._auth_state
        auth_module._auth_state = auth_module._AuthState(
            settings=get_settings(),
            keys=frozenset({"valid_key1", "valid_key2"}),
            metadata={
                "valid_key1": {"source": "settings"},
                "valid_key2": {"source": "settings"},
            },
        )

    def teardown_method(self):
        """Restore global state after each test."""
        auth_module._auth_state = self.original_state

    def test_validate_api_key_valid(self):
        """Test validating a valid API key."""
        assert validate_api_key("valid_key1") is True
        assert validate_api_key("valid_key2") is True

    def test_validate_api_key_invalid(self):
        """Test validating an invalid API key."""
        assert validate_api_key("invalid_key") is False
        assert validate_api_key("") is False
        assert validate_api_key(None) is False
        assert validate_api_key("VALID_KEY1") is False  # Case sensitive

    def test_get_api_key_metadata_valid(self):
        """Test getting metadata for a valid API key."""
        metadata = get_api_key_metadata("valid_key1")
        assert metadata is not None
        assert metadata["source"] == "settings"

    def test_get_api_key_metadata_invalid(self):
        """Test getting metadata for an invalid API key."""
        metadata = get_api_key_metadata("invalid_key")
        assert metadata is None


class TestAuthenticationDependencies:
    """Test FastAPI authentication dependencies."""

    def setup_method(self):
        """Reset global state before each test."""
        # Drop any Settings another test cached while env vars were patched;
        # monkeypatch restores os.environ after the test body, so a cache
        # refilled inside that body would otherwise leak stale values here.
        get_settings.cache_clear()
        self.original_state = auth_module._auth_state
        # Pin the snapshot's settings to the live object so _auth_snapshot()
        # does not rebuild (and discard) the key set below.
        auth_module._auth_state = auth_module._AuthState(
            settings=get_settings(),
            keys=frozenset({"test_key"}),
            metadata={"test_key": {"source": "settings"}},
        )

    def teardown_method(self):
        """Restore global state after each test."""
        get_settings.cache_clear()
        auth_module._auth_state = self.original_state

    @pytest.mark.asyncio
    async def test_authenticate_api_key_valid(self):
        """Test successful API key authentication."""
        result = await authenticate_api_key("test_key")
        assert result == "test_key"

    @pytest.mark.asyncio
    async def test_authenticate_api_key_invalid(self):
        """Test authentication with invalid API key."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await authenticate_api_key("invalid_key")

        assert exc_info.value.status_code == 401
        assert "Invalid" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_authenticate_api_key_missing_header(self):
        """
        A request with no X-API-Key header must be 401, not 500 and not 200.

        Previously APIKeyHeader(auto_error=True) made this unreachable: the
        header-less request was rejected inside FastAPI before this function
        ran at all.
        """
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await authenticate_api_key(None)

        assert exc_info.value.status_code == 401
        assert "Missing" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_authenticate_api_key_disabled(self, monkeypatch):
        """Test authentication when disabled -- including with no header."""
        monkeypatch.setenv("ENABLE_API_KEY_AUTH", "false")
        get_settings.cache_clear()
        try:
            assert await authenticate_api_key("any_key") == "authentication-disabled"
            assert await authenticate_api_key(None) == "authentication-disabled"
        finally:
            get_settings.cache_clear()
            initialize_api_keys(get_settings())

    @pytest.mark.asyncio
    async def test_authenticate_fails_closed_with_no_keys(self):
        """Auth enabled but no keys configured must still reject."""
        from fastapi import HTTPException

        auth_module._auth_state = auth_module._auth_state._replace(keys=frozenset())
        with pytest.raises(HTTPException) as exc_info:
            await authenticate_api_key("anything")
        assert exc_info.value.status_code == 500

    def test_get_current_api_key(self):
        """Test get_current_api_key dependency."""
        result = get_current_api_key("test_key")
        assert result == "test_key"


class TestAPIKeyHeader:
    """Test API key header configuration."""

    def test_api_key_header_configuration(self):
        """Test that API key header is properly configured."""
        assert api_key_header is not None
        assert hasattr(api_key_header, "scheme_name") or hasattr(api_key_header, "model")

    def test_api_key_header_does_not_auto_error(self):
        """
        auto_error must be False.

        With auto_error=True FastAPI rejects a header-less request before
        auth.py runs, so ENABLE_API_KEY_AUTH=false could never take effect --
        disabling auth inverted it.
        """
        assert api_key_header.auto_error is False


class TestGlobalState:
    """Test global state management."""

    def test_global_variables_initialized(self):
        """Test that the auth snapshot is properly initialized."""
        state = _auth_snapshot()
        assert isinstance(state.keys, frozenset)
        assert isinstance(state.metadata, dict)

    def test_auth_state_is_a_single_snapshot(self):
        """
        Auth mode and accepted keys must live in ONE object.

        Reading them from separate globals lets a settings reload concurrent
        with a request mix one config's ENABLE_API_KEY_AUTH with another
        config's key set.
        """
        state = _auth_snapshot()
        assert state.settings is get_settings()
        assert state.keys == frozenset(k for k in [*state.settings.API_KEYS, state.settings.API_KEY] if k)

    def test_initialize_api_keys_reads_settings(self, monkeypatch):
        """initialize_api_keys() merges API_KEYS and API_KEY from Settings."""
        monkeypatch.setenv("API_KEYS", "list-key-a,list-key-b")
        monkeypatch.setenv("API_KEY", "single-key")
        get_settings.cache_clear()
        try:
            keys = initialize_api_keys(get_settings())
            assert keys == {"list-key-a", "list-key-b", "single-key"}
        finally:
            get_settings.cache_clear()
            initialize_api_keys(get_settings())

    def test_stale_rebuild_cannot_reinstate_rotated_keys(self, monkeypatch):
        """
        A slow refresh must not overwrite a newer snapshot.

        Deterministic interleaving: a thread enters _auth_snapshot() with the
        OLD settings, is held at the lock while the main thread rotates the
        keys and publishes a new snapshot, then proceeds. It must observe the
        rotation rather than republishing the credentials it read first --
        otherwise a rotated-out key would authenticate again.
        """
        import threading

        monkeypatch.setenv("API_KEYS", "old-key")
        get_settings.cache_clear()
        try:
            initialize_api_keys(get_settings())
            assert validate_api_key("old-key") is True

            entered = threading.Event()
            may_proceed = threading.Event()
            result = {}

            real_lock = auth_module._refresh_lock

            class _GatedLock:
                """Signals on entry, then blocks until the test releases it."""

                def __enter__(self):
                    entered.set()
                    may_proceed.wait(timeout=5)
                    return real_lock.__enter__()

                def __exit__(self, *exc):
                    return real_lock.__exit__(*exc)

            def _slow_refresh():
                monkeypatch.setattr(auth_module, "_refresh_lock", _GatedLock(), raising=False)
                result["keys"] = auth_module._auth_snapshot().keys

            # Force the snapshot to look stale so _auth_snapshot() takes the
            # rebuild path.
            auth_module._auth_state = auth_module._auth_state._replace(settings=None)

            worker = threading.Thread(target=_slow_refresh)
            worker.start()
            assert entered.wait(timeout=5), "refresh thread never reached the lock"

            # Rotate while the refresh thread is parked.
            monkeypatch.setenv("API_KEYS", "new-key")
            get_settings.cache_clear()
            initialize_api_keys(get_settings())

            may_proceed.set()
            worker.join(timeout=5)
            assert not worker.is_alive()

            # The parked thread must not have republished "old-key".
            assert "old-key" not in result["keys"]
            assert "new-key" in result["keys"]
            assert validate_api_key("old-key") is False
            assert validate_api_key("new-key") is True
        finally:
            get_settings.cache_clear()
            initialize_api_keys(get_settings())

    def test_settings_reload_refreshes_key_set(self, monkeypatch):
        """
        Reloading settings must refresh the accepted keys.

        The old code snapshotted keys once at import time, so a test (or a
        runtime reload) that changed configuration was silently ignored.
        """
        monkeypatch.setenv("API_KEYS", "rotated-key")
        get_settings.cache_clear()
        try:
            # validate_api_key must see the reload on its own, with no
            # authenticated request needed to trigger a refresh first.
            assert validate_api_key("rotated-key") is True
            assert auth_module._auth_snapshot().settings.API_KEYS == ["rotated-key"]
        finally:
            get_settings.cache_clear()
            initialize_api_keys(get_settings())


class TestBasicFunctionality:
    """Test basic authentication functionality."""

    def test_validate_api_key_with_empty_set(self):
        """Validation against an empty key set rejects everything."""
        original = auth_module._auth_state
        auth_module._auth_state = original._replace(settings=get_settings(), keys=frozenset())
        try:
            assert validate_api_key("any_key") is False
        finally:
            auth_module._auth_state = original

    def test_get_api_key_metadata_with_empty_mapping(self):
        """Metadata lookup against an empty mapping returns None."""
        original = auth_module._auth_state
        auth_module._auth_state = original._replace(settings=get_settings(), metadata={})
        try:
            assert get_api_key_metadata("any_key") is None
        finally:
            auth_module._auth_state = original
