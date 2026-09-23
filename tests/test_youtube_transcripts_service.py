"""Timeout behaviour of the YouTube transcripts service.

youtube-transcript-api never passes ``timeout=`` to requests, whose default is
to wait forever. These tests pin the fix: every request made through the
service's http client carries a finite timeout, and a timeout is retried and
then reported as a 504 rather than a 500 or a proxy 502.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from requests import Session
from requests.exceptions import ConnectTimeout, ReadTimeout

from app.services import youtube_transcripts_service as yts


def _capture_send_timeout(session, **request_kwargs):
    """Issue a GET through ``session`` and return the timeout handed to send()."""
    with patch.object(Session, "send", return_value=MagicMock()) as send:
        session.get("https://www.youtube.com/watch?v=x", **request_kwargs)
    return send.call_args.kwargs["timeout"]


def test_session_applies_default_timeout():
    session = yts._TimeoutSession((3.0, 7.0))
    assert _capture_send_timeout(session) == (3.0, 7.0)


def test_session_keeps_an_explicit_timeout():
    session = yts._TimeoutSession((3.0, 7.0))
    assert _capture_send_timeout(session, timeout=1.5) == 1.5


@pytest.mark.parametrize("proxy_url", [None, "http://user:pw@proxy.example:8080"])
def test_api_is_built_with_a_timeout_session(monkeypatch, proxy_url):
    settings = MagicMock(HTTP_CONNECTION_TIMEOUT=4.0, HTTP_READ_TIMEOUT=9.0)
    monkeypatch.setattr(yts, "get_settings", lambda: settings)

    with patch.object(yts, "YouTubeTranscriptApi") as api_cls:
        yts.YouTubeTranscriptsService()._get_youtube_api(proxy_url)

    client = api_cls.call_args.kwargs["http_client"]
    assert isinstance(client, yts._TimeoutSession)
    assert client._default_timeout == (4.0, 9.0)


@pytest.mark.parametrize("exc", [ReadTimeout("slow"), ConnectTimeout("slow")])
def test_timeout_maps_to_504(exc):
    # ConnectTimeout is also a ConnectionError; it must not be reported as a
    # proxy misconfiguration (502).
    with pytest.raises(HTTPException) as info:
        yts.YouTubeTranscriptsService()._handle_youtube_exception(exc, "vid", "fetching transcript")
    assert info.value.status_code == 504


def test_timeout_is_retried(monkeypatch):
    service = yts.YouTubeTranscriptsService()
    monkeypatch.setattr(service, "_get_current_api", lambda: object())
    func = MagicMock(side_effect=[ReadTimeout("slow"), ["ok"]])

    assert service._execute_with_retry(func, "vid", "fetching transcript") == ["ok"]
    assert func.call_count == 2


def test_timeout_on_every_attempt_is_a_504(monkeypatch):
    service = yts.YouTubeTranscriptsService()
    monkeypatch.setattr(service, "_get_current_api", lambda: object())
    func = MagicMock(side_effect=ReadTimeout("slow"))

    with pytest.raises(HTTPException) as info:
        service._execute_with_retry(func, "vid", "fetching transcript")
    assert info.value.status_code == 504
    assert func.call_count == yts.MAX_RETRY_ATTEMPTS
