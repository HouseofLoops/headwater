"""Tests for proxy selection.

The bug under test: proxy config was read with os.getenv at import time, but
pydantic-settings never exports .env values into os.environ. Under a bare
`uvicorn main:app` run every request silently went out un-proxied while the
configuration claimed otherwise.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.core import proxy as proxy_module


def _settings(**kw):
    """A stand-in Settings object with only the proxy fields that matter."""
    base = {"ENABLE_PROXY": False, "PROXY_URLS": None, "PROXY_URL": None}
    base.update(kw)
    return SimpleNamespace(**base)


def _with(**kw):
    return patch("app.core.config.get_settings", return_value=_settings(**kw))


@pytest.fixture(autouse=True)
def _reset_cursor():
    """The round-robin cursor is module state; isolate tests from each other."""
    proxy_module._proxy_iter = None
    proxy_module._proxy_iter_source = ()
    yield
    proxy_module._proxy_iter = None
    proxy_module._proxy_iter_source = ()


class TestReadsFromSettingsNotEnviron:
    """The regression that made proxying inert outside Docker."""

    def test_proxy_is_found_from_settings_with_empty_environ(self, monkeypatch):
        # Simulate a bare uvicorn run: .env is loaded into Settings, and
        # os.environ is empty. The old implementation returned None here.
        monkeypatch.delenv("PROXY_URLS", raising=False)
        monkeypatch.delenv("ENABLE_PROXY", raising=False)
        with _with(ENABLE_PROXY=True, PROXY_URLS="http://proxy.example:8030"):
            assert proxy_module.get_proxy_sync() == "http://proxy.example:8030"

    def test_config_change_takes_effect_without_reimport(self):
        # Import-time constants could never do this.
        with _with(ENABLE_PROXY=True, PROXY_URLS="http://a.example:1"):
            assert proxy_module.get_proxy_sync() == "http://a.example:1"
        with _with(ENABLE_PROXY=True, PROXY_URLS="http://b.example:2"):
            assert proxy_module.get_proxy_sync() == "http://b.example:2"

    def test_settings_failure_degrades_to_no_proxy(self):
        with patch("app.core.config.get_settings", side_effect=RuntimeError("boom")):
            assert proxy_module.get_proxy_sync() is None
            assert proxy_module.is_proxy_enabled() is False


class TestDisabled:
    def test_returns_none_when_disabled(self):
        with _with(ENABLE_PROXY=False, PROXY_URLS="http://proxy.example:8030"):
            assert proxy_module.get_proxy_sync() is None
            assert proxy_module.rotate_proxy() is None

    def test_returns_none_when_enabled_but_unconfigured(self):
        with _with(ENABLE_PROXY=True, PROXY_URLS=""):
            assert proxy_module.get_proxy_sync() is None

    def test_is_proxy_enabled_requires_both(self):
        with _with(ENABLE_PROXY=True, PROXY_URLS=""):
            assert proxy_module.is_proxy_enabled() is False
        with _with(ENABLE_PROXY=False, PROXY_URLS="http://a.example:1"):
            assert proxy_module.is_proxy_enabled() is False
        with _with(ENABLE_PROXY=True, PROXY_URLS="http://a.example:1"):
            assert proxy_module.is_proxy_enabled() is True


class TestParsing:
    def test_comma_separated_list(self):
        with _with(ENABLE_PROXY=True, PROXY_URLS="http://a.example:1, http://b.example:2"):
            assert proxy_module.get_available_proxies() == [
                "http://a.example:1",
                "http://b.example:2",
            ]

    def test_malformed_urls_are_dropped(self):
        with _with(ENABLE_PROXY=True, PROXY_URLS="not-a-url,http://ok.example:1,ftp://x"):
            assert proxy_module.get_available_proxies() == ["http://ok.example:1"]

    def test_legacy_singular_proxy_url_is_accepted(self):
        # The README documented PROXY_URL for a long time; Settings declares both.
        with _with(ENABLE_PROXY=True, PROXY_URL="http://legacy.example:9"):
            assert proxy_module.get_proxy_sync() == "http://legacy.example:9"

    def test_both_forms_merge_without_duplicates(self):
        with _with(
            ENABLE_PROXY=True,
            PROXY_URLS="http://a.example:1,http://b.example:2",
            PROXY_URL="http://a.example:1",
        ):
            assert proxy_module.get_available_proxies() == [
                "http://a.example:1",
                "http://b.example:2",
            ]

    def test_list_valued_setting_is_accepted(self):
        with _with(ENABLE_PROXY=True, PROXY_URLS=["http://a.example:1"]):
            assert proxy_module.get_available_proxies() == ["http://a.example:1"]


class TestRotation:
    @pytest.mark.asyncio
    async def test_round_robin_cycles(self):
        with _with(ENABLE_PROXY=True, PROXY_URLS="http://a.example:1,http://b.example:2"):
            first = await proxy_module.get_proxy()
            second = await proxy_module.get_proxy()
            third = await proxy_module.get_proxy()
        assert [first, second, third] == [
            "http://a.example:1",
            "http://b.example:2",
            "http://a.example:1",
        ]

    def test_rotate_advances(self):
        with _with(ENABLE_PROXY=True, PROXY_URLS="http://a.example:1,http://b.example:2"):
            assert proxy_module.rotate_proxy() == "http://a.example:1"
            assert proxy_module.rotate_proxy() == "http://b.example:2"

    def test_cursor_rebuilds_when_list_changes(self):
        # A stale cursor would keep yielding proxies that are no longer configured.
        with _with(ENABLE_PROXY=True, PROXY_URLS="http://old.example:1"):
            assert proxy_module.rotate_proxy() == "http://old.example:1"
        with _with(ENABLE_PROXY=True, PROXY_URLS="http://new.example:2"):
            assert proxy_module.rotate_proxy() == "http://new.example:2"


class TestLegacyModuleAttributes:
    """Existing callers do `from app.core.proxy import ENABLE_PROXY`."""

    def test_enable_proxy_resolves_from_settings(self):
        with _with(ENABLE_PROXY=True, PROXY_URLS="http://a.example:1"):
            assert proxy_module.ENABLE_PROXY is True
        with _with(ENABLE_PROXY=False):
            assert proxy_module.ENABLE_PROXY is False

    def test_proxy_list_resolves_from_settings(self):
        with _with(ENABLE_PROXY=True, PROXY_URLS="http://a.example:1"):
            assert proxy_module.PROXY_LIST == ["http://a.example:1"]
            assert proxy_module.AVAILABLE_PROXIES == ["http://a.example:1"]

    def test_unknown_attribute_still_raises(self):
        with pytest.raises(AttributeError):
            proxy_module.NOT_A_REAL_SETTING  # noqa: B018 - the attribute access itself is what must raise
