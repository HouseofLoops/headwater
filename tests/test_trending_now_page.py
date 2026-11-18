"""Trending Now is read from the trending page, not batchexecute RPC i0OFE.

Google answers i0OFE with a null payload and RPC status [7] (PERMISSION_DENIED)
since early November 2026; trendspy then crashed on ``json.loads(None)`` and every
/trending-now request was a 502. The page embeds the same rows in an
``AF_initDataCallback`` block.
"""

import json
from unittest.mock import MagicMock

import pytest
import requests

from app.api.google_trends.google_trends_api import (
    TRENDING_PAGE_URL,
    UpstreamUnavailable,
    fetch_trending_now_rows,
    parse_trending_page,
)

# A real row from trends.google.com/trending?geo=US&hours=4 (2026-11-18).
ROW = [
    "unh stock",
    None,
    "US",
    [1795014600],
    None,
    None,
    1000,
    None,
    75,
    ["unh stock"],
    [3],
    [[4941241029, "en", "US"]],
    "unh stock",
]


def trending_page(rows=None) -> str:
    """Build a page shaped like the live one: a geo block, then the trends block."""
    rows = [ROW] if rows is None else rows
    return (
        "<html><script>"
        "AF_initDataCallback({key: 'ds:1', hash: '1', data:[\"United States\"], sideChannel: {}});"
        "</script><script>"
        f"AF_initDataCallback({{key: 'ds:0', hash: '2', data:{json.dumps([None, rows])}, sideChannel: {{}}}});"
        "</script></html>"
    )


def trends_obj(html: str) -> MagicMock:
    obj = MagicMock()
    obj.language = "en"
    obj._get.return_value = MagicMock(text=html)
    return obj


def test_parse_finds_the_trends_block_not_the_geo_block():
    assert parse_trending_page(trending_page()) == [ROW]


def test_parse_returns_an_empty_list_when_nothing_is_trending():
    assert parse_trending_page(trending_page(rows=[])) == []


def test_parse_returns_none_when_the_block_is_missing():
    assert parse_trending_page("<html>no data here</html>") is None


def test_fetch_requests_the_page_with_geo_hours_and_language():
    obj = trends_obj(trending_page())
    fetch_trending_now_rows(obj, geo="GB", hours=48)
    obj._get.assert_called_once_with(TRENDING_PAGE_URL, {"geo": "GB", "hours": 48, "hl": "en"})


def test_fetch_returns_trendspy_keywords():
    result = fetch_trending_now_rows(trends_obj(trending_page()), geo="US")
    assert len(result) == 1
    keyword = result[0]
    assert keyword.keyword == "unh stock"
    assert keyword.volume == 1000
    assert keyword.news_tokens == [[4941241029, "en", "US"]]


def test_fetch_raises_unavailable_when_the_page_changes_shape():
    with pytest.raises(UpstreamUnavailable):
        fetch_trending_now_rows(trends_obj("<html>redesigned</html>"), geo="US")


def test_fetch_raises_unavailable_on_an_unreadable_row():
    with pytest.raises(UpstreamUnavailable):
        fetch_trending_now_rows(trends_obj(trending_page(rows=[["only a keyword"]])), geo="US")


def test_fetch_lets_http_errors_through_for_classification():
    obj = MagicMock()
    obj.language = "en"
    obj._get.side_effect = requests.HTTPError("429")
    with pytest.raises(requests.HTTPError):
        fetch_trending_now_rows(obj, geo="US")
