"""Call-site log scrubbing on the cache path.

Cache keys embed caller-supplied query values. These tests log through the
cache manager with a newline-bearing key and assert that no emitted record
contains a raw line break. caplog's handler has no LogInjectionFilter, so a
pass here comes from the call-site scrub(), not the root-logger safety net.
"""

import logging
from unittest.mock import MagicMock

import pytest

from app.core.cache_manager import CacheManager

FORGED = "q\nINFO:root:admin login succeeded"


def _memory_manager() -> CacheManager:
    settings = MagicMock(ENABLE_CACHE=True, CACHE_TTL=600, REDIS_URL=None)
    return CacheManager(settings=settings)


def _assert_single_line(caplog):
    messages = [r.getMessage() for r in caplog.records if r.name == "app.core.cache_manager"]
    assert messages, "expected the cache manager to log"
    for message in messages:
        assert "\n" not in message and "\r" not in message, message
    assert any("\\x0a" in m for m in messages)


@pytest.mark.asyncio
async def test_set_hit_miss_and_delete_logs_are_single_line(caplog):
    manager = _memory_manager()
    with caplog.at_level(logging.DEBUG, logger="app.core.cache_manager"):
        await manager.set(FORGED, "value", ttl=60)
        assert await manager.get(FORGED) == "value"
        await manager.delete(FORGED)
        assert await manager.get(FORGED) is None
    _assert_single_line(caplog)


@pytest.mark.asyncio
async def test_fetch_error_log_is_single_line(caplog, monkeypatch):
    from app.core import cache_manager as cm

    async def failing_fetch():
        raise RuntimeError("upstream said:\nINFO:root:forged")

    monkeypatch.setattr(cm, "cache_manager", _memory_manager())
    with caplog.at_level(logging.DEBUG, logger="app.core.cache_manager"), pytest.raises(RuntimeError):
        await cm.get_cached_or_fetch(FORGED, failing_fetch)
    _assert_single_line(caplog)
