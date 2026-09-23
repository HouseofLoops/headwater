"""Shared pytest configuration and fixtures for the Headwater test suite.

This file exists to make the suite *deterministic*. Two guarantees, both
enforced here rather than left to each test author to remember:

1. **Tests never read a developer's real ``.env``.**
   ``app.core.config.Settings`` declares ``model_config = {"env_file": ".env"}``.
   Whatever happens to be in a contributor's (or a CI runner's) ``.env`` would
   otherwise leak into every ``Settings()`` construction, so the same test can
   pass on one machine and fail on another. :func:`pytest_configure` pins
   ``env_file`` to ``None`` for the duration of the test session, before any
   test module is imported, and clears the ``get_settings`` LRU cache.

2. **Tests never make outbound network calls.**
   An autouse fixture blocks socket connections to anything that is not
   loopback. A test that genuinely needs the network must say so with
   ``@pytest.mark.allow_network`` - which makes the exception visible in review
   instead of hiding it in a flaky CI run.

Everything else here is opt-in: fixtures that existing tests can adopt
incrementally (``test_client``, ``settings``, ``fake_redis`` and friends). No
fixture below is autouse except the two guards, so adding this file does not
change the behaviour of any test that does not ask for it.

Deliberately NOT done here:

* No module-level ``from app.main import app``. There is no ``app.main``; the
  ASGI application lives in the top-level ``main`` module. An import at module
  scope would also make a single broken import take down collection for the
  entire suite. All app imports below are lazy, inside fixtures.
* No autouse async cleanup fixture and no ``event_loop`` override. Both are
  patterns from the abandoned ``code-review-improvements`` branch that either
  no longer work with pytest-asyncio 1.x or silently do nothing.
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Import path
# ---------------------------------------------------------------------------
# The ASGI app lives in the repository root as ``main.py`` (``main:app``), and
# the package under test is ``app``. Both need the repository root on
# ``sys.path`` regardless of the directory pytest was invoked from.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# Environment defaults
# ---------------------------------------------------------------------------
# Only variables that are NOT ``Settings`` fields are injected. Anything that
# maps onto a settings field is stripped instead (see ``_isolate_settings``), so
# that ``Settings()`` in a test always resolves to its declared defaults. Adding
# opinionated values here instead would silently change the meaning of every
# test that asserts on a default.
_TEST_ENV_EXTRAS = {
    "TESTING": "true",
}


_EMPTY_ENV_FILE: Path | None = None


def _isolate_settings() -> None:
    """Stop ``Settings()`` from reading the developer's real ``.env``.

    ``Settings.model_config["env_file"]`` is ``".env"``, resolved relative to
    the working directory *at instantiation time*, so there is no way to opt out
    from outside once such a file exists next to the tests. Whatever a
    contributor happens to have configured locally would otherwise decide the
    outcome of every test that constructs ``Settings()``.

    The fix is to repoint ``env_file`` at a guaranteed-empty temporary file
    rather than at ``None``:

    * empty file -> pydantic-settings reads it and finds nothing, which is
      exactly the "no .env" behaviour we want, on every machine;
    * ``None`` would ALSO work for pydantic, but ``main.py`` does
      ``".env" in settings.model_config.get("env_file", [])`` on the
      ``/config-sources`` route, which raises ``TypeError`` on ``None``. The
      temporary file keeps its name ending in ``.env`` so that route keeps
      answering exactly what it answered before. Fixing that expression is an
      application change and is not this file's business.

    Exported shell variables are deliberately left alone. Several existing
    tests drive configuration through ``os.environ`` and rely on values set by
    earlier fixtures; stripping them here changes results for reasons unrelated
    to the code under test.
    """
    global _EMPTY_ENV_FILE

    try:
        from app.core import config as app_config
    except Exception as exc:  # pragma: no cover - surfaced as a clear failure
        raise RuntimeError(
            "tests/conftest.py could not import app.core.config; the test suite "
            f"cannot guarantee .env isolation. Original error: {exc!r}"
        ) from exc

    handle, path = tempfile.mkstemp(prefix="headwater-tests-", suffix=".env")
    os.close(handle)
    _EMPTY_ENV_FILE = Path(path)

    app_config.Settings.model_config["env_file"] = str(_EMPTY_ENV_FILE)

    # app/core/config.py ends with `settings = get_settings()`, so importing it
    # above ALREADY built a Settings instance from the developer's real .env --
    # before the repoint on the line above could take effect. Repointing
    # env_file alone therefore isolated future constructions while leaving the
    # module-level global (and the lru_cache behind get_settings) holding the
    # leaked values. Clear the cache and rebuild the global so every access
    # path sees the isolated file.
    app_config.get_settings.cache_clear()
    app_config.settings = app_config.get_settings()


def pytest_configure(config: pytest.Config) -> None:
    """Runs before collection, so before any test module imports ``Settings``."""
    for key, value in _TEST_ENV_EXTRAS.items():
        os.environ.setdefault(key, value)
    _isolate_settings()


def pytest_unconfigure(config: pytest.Config) -> None:
    if _EMPTY_ENV_FILE is not None:
        _EMPTY_ENV_FILE.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Network guard
# ---------------------------------------------------------------------------
_LOOPBACK = {"127.0.0.1", "::1", "localhost", "localhost.localdomain", "testserver", ""}

_real_socket_connect = socket.socket.connect
_real_socket_connect_ex = socket.socket.connect_ex
_real_create_connection = socket.create_connection


class NetworkAccessDenied(RuntimeError):
    """Raised when a test tries to open a non-loopback socket."""


def _host_of(address: Any) -> str:
    if isinstance(address, tuple) and address:
        return str(address[0])
    return str(address)


def _is_allowed(address: Any) -> bool:
    # Unix domain sockets and abstract sockets are addressed by str/bytes and
    # never leave the machine.
    if isinstance(address, (str, bytes)):
        return True
    return _host_of(address) in _LOOPBACK


def _blocked(address: Any) -> NetworkAccessDenied:
    return NetworkAccessDenied(
        f"Blocked outbound network connection to {_host_of(address)!r} during a test. "
        "Mock the HTTP client, or mark the test with @pytest.mark.allow_network "
        "if it genuinely must reach the network."
    )


@pytest.fixture(autouse=True)
def _no_outbound_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """Fail loudly instead of hitting the internet.

    Loopback stays open so that ``TestClient``, local uvicorn instances and any
    localhost service used by an integration test keep working.
    """
    if request.node.get_closest_marker("allow_network"):
        yield
        return

    def guarded_connect(self, address):  # type: ignore[no-untyped-def]
        if not _is_allowed(address):
            raise _blocked(address)
        return _real_socket_connect(self, address)

    def guarded_connect_ex(self, address):  # type: ignore[no-untyped-def]
        if not _is_allowed(address):
            raise _blocked(address)
        return _real_socket_connect_ex(self, address)

    def guarded_create_connection(address, *args, **kwargs):  # type: ignore[no-untyped-def]
        if not _is_allowed(address):
            raise _blocked(address)
        return _real_create_connection(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect, raising=True)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex, raising=True)
    monkeypatch.setattr(socket, "create_connection", guarded_create_connection, raising=True)
    yield


# ---------------------------------------------------------------------------
# Settings fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def settings():
    """A real ``Settings`` instance built purely from the test environment."""
    from app.core.config import Settings

    return Settings()


@pytest.fixture
def override_settings(monkeypatch: pytest.MonkeyPatch):
    """Factory: override settings values for the duration of one test.

    ::

        def test_cache_disabled(override_settings):
            s = override_settings(ENABLE_CACHE=False, CACHE_TTL=1)
            assert s.ENABLE_CACHE is False

    The override is installed on ``app.core.config.get_settings`` so that code
    resolving settings through the dependency sees it too, and is undone
    automatically when the test ends.
    """
    from app.core import config as app_config

    def _apply(**overrides: Any):
        instance = app_config.Settings()
        for key, value in overrides.items():
            object.__setattr__(instance, key, value)

        monkeypatch.setattr(app_config, "get_settings", lambda: instance, raising=True)
        return instance

    return _apply


# ---------------------------------------------------------------------------
# Application / client fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def fastapi_app():
    """The real ASGI application (``main:app``), imported lazily."""
    import main

    return main.app


@pytest.fixture
def test_client(fastapi_app):
    """A ``TestClient`` bound to the real application.

    Uses a context manager so FastAPI startup and shutdown events actually run -
    a plain ``TestClient(app)`` skips them, which is how "it works in tests but
    not in production" bugs get through.
    """
    from fastapi.testclient import TestClient

    with TestClient(fastapi_app) as client:
        yield client


@pytest.fixture
def api_headers(settings):
    """Headers carrying a valid API key for the test environment."""
    keys = getattr(settings, "API_KEYS", None) or ["test-api-key"]
    first = keys[0] if isinstance(keys, (list, tuple)) else str(keys)
    return {
        "x-api-key": first,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


# ---------------------------------------------------------------------------
# Redis fixtures
# ---------------------------------------------------------------------------
class FakeRedis:
    """A minimal in-memory stand-in for ``redis.asyncio.Redis``.

    Covers the subset the application actually uses. It is intentionally small:
    a fake that reimplements Redis is a second bug surface. Add methods here as
    the application starts using them, not speculatively.
    """

    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.expiries: dict[str, int] = {}
        self.closed = False

    async def ping(self) -> bool:
        return True

    async def get(self, key: str) -> Any:
        return self.store.get(key)

    async def set(self, key: str, value: Any, ex: int | None = None, **_: Any) -> bool:
        self.store[key] = value
        if ex is not None:
            self.expiries[key] = ex
        return True

    # redis-py spells this setex(name, time, value)
    async def setex(self, key: str, time: int, value: Any) -> bool:
        return await self.set(key, value, ex=time)

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self.store:
                del self.store[key]
                self.expiries.pop(key, None)
                removed += 1
        return removed

    async def exists(self, *keys: str) -> int:
        return sum(1 for key in keys if key in self.store)

    async def expire(self, key: str, seconds: int) -> bool:
        if key not in self.store:
            return False
        self.expiries[key] = seconds
        return True

    async def ttl(self, key: str) -> int:
        if key not in self.store:
            return -2
        return self.expiries.get(key, -1)

    async def keys(self, pattern: str = "*") -> list[str]:
        import fnmatch

        return [key for key in self.store if fnmatch.fnmatch(key, pattern)]

    async def scan_iter(self, match: str = "*", **_: Any):
        for key in await self.keys(match):
            yield key

    async def flushdb(self) -> bool:
        self.store.clear()
        self.expiries.clear()
        return True

    async def incr(self, key: str, amount: int = 1) -> int:
        value = int(self.store.get(key, 0)) + amount
        self.store[key] = value
        return value

    async def close(self) -> None:
        self.closed = True

    aclose = close


@pytest.fixture
def fake_redis() -> FakeRedis:
    """A fresh in-memory Redis double."""
    return FakeRedis()


@pytest.fixture
def patched_redis(fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    """Make every ``redis.asyncio`` client in the process the fake one.

    Use this in tests that exercise caching or rate limiting without wanting a
    real Redis (and without the network guard rejecting the connection).
    """
    # importorskip binds the module or skips; there is no path on which
    # ``redis_asyncio`` is used unbound.
    redis_asyncio = pytest.importorskip("redis.asyncio", reason="redis is not installed")

    monkeypatch.setattr(redis_asyncio, "from_url", lambda *a, **k: fake_redis, raising=False)
    monkeypatch.setattr(redis_asyncio, "Redis", lambda *a, **k: fake_redis, raising=False)
    return fake_redis


# ---------------------------------------------------------------------------
# HTTP mocking helpers
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_http_response():
    """A ``MagicMock`` shaped like a successful ``httpx`` / ``requests`` response."""

    def _make(status_code: int = 200, json_body: Any = None, text: str = ""):
        response = MagicMock()
        response.status_code = status_code
        response.text = text or ""
        response.json.return_value = {} if json_body is None else json_body
        response.headers = {"Content-Type": "application/json"}
        response.raise_for_status.return_value = None
        return response

    return _make


@pytest.fixture
def mock_async_client(mock_http_response):
    """An ``AsyncMock`` shaped like ``httpx.AsyncClient``, returning 200 by default."""
    client = AsyncMock()
    client.get.return_value = mock_http_response()
    client.post.return_value = mock_http_response()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    return client


@pytest.fixture(autouse=True)
def _reset_rate_limit_state():
    """Clear the process-global rate-limit store between every test.

    ``app.core.rate_limiter`` keeps its buckets in a module-level dict, and
    ``RateLimitMiddleware`` counts *every* request -- so without this, requests
    made by one test consume another test's budget and unrelated tests start
    seeing 429s depending on execution order. That ordering sensitivity is the
    same fact as CRT-8's "a per-process store multiplies limits by worker
    count", observed from the test side.

    Autouse and global rather than per-file: a reset that each test module has
    to remember is a reset that the next new test file will forget.
    """
    try:
        from app.core.rate_limiter import reset_rate_limit_state
    except ImportError:  # pragma: no cover - module always present in-tree
        yield
        return

    reset_rate_limit_state()
    try:
        yield
    finally:
        reset_rate_limit_state()
