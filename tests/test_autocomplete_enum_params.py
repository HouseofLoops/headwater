"""Enum-typed query params must reach Google as their wire values.

``/google-autocomplete/autocomplete`` put the ``ds`` member itself (not
``ds.value``) into the outbound params dict and the cache key. With the old
``class DataSource(str, Enum)`` httpx 0.28 stringifies it with ``str()``, which
yields ``"DataSource.YOUTUBE"``, so Google received ``ds=DataSource.YOUTUBE``
and silently ignored the data-source filter. ``StrEnum`` stringifies to the
value, so the request carries ``ds=yt``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.google_autocomplete import google_autocomplete_api as api
from app.core.auth import get_api_key
from app.core.rate_limiter import rate_limit
from app.schemas.enums import (
    ClientType,
    DataSource,
    HumanFriendlyBatchPeriod,
    OutputFormat,
    SafeSearch,
    SearchClient,
)


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch):
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = request.url
        return httpx.Response(200, json=["python", ["python tutorial"]])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    manager = MagicMock()

    async def get_client(_proxy=None):
        return client

    manager.get_client = get_client
    manager.get_request_count.return_value = 1
    manager.get_connection_stats.return_value = {}

    sanitizer = MagicMock()
    sanitizer.settings.INPUT_SANITIZATION_ENABLED = False

    async def no_proxy():
        return None

    async def passthrough(cache_key, fetch):
        seen["cache_key"] = cache_key
        return await fetch()

    monkeypatch.setattr(api, "get_proxy", no_proxy)
    monkeypatch.setattr(api, "get_http_client_manager", lambda: manager)
    monkeypatch.setattr(api, "get_input_sanitizer", lambda: sanitizer)
    monkeypatch.setattr(api, "get_cached_or_fetch", passthrough)

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_api_key] = lambda: "test-api-key"
    app.dependency_overrides[rate_limit] = lambda: None
    with TestClient(app) as tc:
        yield tc, seen


def test_ds_is_sent_upstream_as_its_value(captured):
    tc, seen = captured
    resp = tc.get(
        "/autocomplete",
        params={"q": "python", "output": "chrome", "ds": "yt", "client": "chrome", "safe": "off", "sclient": "psy-ab"},
    )
    assert resp.status_code == 200, resp.text

    url = seen["url"]
    assert url.params["ds"] == "yt"
    assert "DataSource" not in str(url)
    assert url.params["output"] == "chrome"
    assert url.params["client"] == "chrome"
    assert url.params["safe"] == "off"
    assert url.params["sclient"] == "psy-ab"

    assert "ds=yt" in seen["cache_key"]
    assert "DataSource." not in seen["cache_key"]


@pytest.mark.parametrize(
    "member",
    [
        DataSource.YOUTUBE,
        OutputFormat.CHROME,
        ClientType.FIREFOX,
        SafeSearch.OFF,
        SearchClient.PSY_AB,
        HumanFriendlyBatchPeriod.past_4h,
    ],
)
def test_enum_members_stringify_to_their_value(member):
    assert str(member) == member.value
    assert f"{member}" == member.value
    assert httpx.Request("GET", "https://e.x", params={"p": member}).url.params["p"] == member.value
