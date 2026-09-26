"""Google Autocomplete answering 429 is a 429 problem with Retry-After, not a 500.

The endpoint raised ``HTTPException(429)`` for Google's status and then caught
it in its own ``except Exception`` block, so callers got ``500 Internal Server
Error: 429: Failed to retrieve suggestions`` and the log showed a traceback.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.google_autocomplete import google_autocomplete_api as api
from app.core.auth import get_api_key
from app.core.exceptions import configure_exception_handlers
from app.core.rate_limiter import rate_limit


@pytest.fixture
def autocomplete(monkeypatch: pytest.MonkeyPatch):
    """Return ``(client, set_upstream)``; ``set_upstream(response)`` fixes Google's answer."""
    upstream: dict[str, httpx.Response] = {"response": httpx.Response(200, json=["python", []])}

    def handler(request: httpx.Request) -> httpx.Response:
        return upstream["response"]

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    manager = MagicMock()

    async def get_client(_proxy=None):
        return http_client

    manager.get_client = get_client

    sanitizer = MagicMock()
    sanitizer.settings.INPUT_SANITIZATION_ENABLED = False

    async def no_proxy():
        return None

    async def passthrough(cache_key, fetch):
        return await fetch()

    monkeypatch.setattr(api, "get_proxy", no_proxy)
    monkeypatch.setattr(api, "get_http_client_manager", lambda: manager)
    monkeypatch.setattr(api, "get_input_sanitizer", lambda: sanitizer)
    monkeypatch.setattr(api, "get_cached_or_fetch", passthrough)

    app = FastAPI()
    configure_exception_handlers(app)
    app.include_router(api.router)
    app.dependency_overrides[get_api_key] = lambda: "test-api-key"
    app.dependency_overrides[rate_limit] = lambda: None

    def set_upstream(response: httpx.Response) -> None:
        upstream["response"] = response

    with TestClient(app) as tc:
        yield tc, set_upstream


def test_google_429_with_retry_after_is_passed_on(autocomplete):
    client, set_upstream = autocomplete
    set_upstream(httpx.Response(429, text="<html>sorry</html>", headers={"Retry-After": "30"}))

    response = client.get("/autocomplete", params={"q": "python"})

    assert response.status_code == 429, response.text
    assert response.headers["Retry-After"] == "30"
    assert response.headers["Content-Type"].startswith("application/problem+json")
    body = response.json()
    assert body["type"] == "https://headwater.com/problems/upstream_rate_limited"
    assert body["upstream"] == "Google Autocomplete"
    assert body["retry_after"] == 30


def test_google_429_without_retry_after_uses_the_default(autocomplete, override_settings):
    override_settings(UPSTREAM_RETRY_AFTER_SECONDS=90)
    client, set_upstream = autocomplete
    set_upstream(httpx.Response(429, text=""))

    response = client.get("/autocomplete", params={"q": "python"})

    assert response.status_code == 429, response.text
    assert response.headers["Retry-After"] == "90"
    assert response.json()["retry_after"] == 90


def test_a_normal_answer_is_unaffected(autocomplete):
    client, set_upstream = autocomplete
    set_upstream(httpx.Response(200, json=["python", ["python tutorial"]]))

    response = client.get("/autocomplete", params={"q": "python", "output": "chrome", "client": "chrome"})

    assert response.status_code == 200, response.text
