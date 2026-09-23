"""is_googleusercontent_url: hostname check replacing a substring match."""

import pytest

from app.core.url_utils import is_googleusercontent_url


@pytest.mark.parametrize(
    "url",
    [
        "https://lh3.googleusercontent.com/p/AF1QipN=w408-h306-k-no",
        "https://lh5.googleusercontent.com/gps-cs-s/AC9h4n=w800-h600",
        "//lh3.googleusercontent.com/p/AF1QipN=w80-h106",  # protocol-relative src
        "https://googleusercontent.com/x",
        "HTTPS://LH3.GOOGLEUSERCONTENT.COM/p/abc",
    ],
)
def test_accepts_googleusercontent_hosts(url):
    assert is_googleusercontent_url(url)


@pytest.mark.parametrize(
    "url",
    [
        # All of these contain the substring the old check looked for.
        "https://evil.example/?next=googleusercontent.com",
        "https://evil.example/googleusercontent.com/p/abc",
        "https://googleusercontent.com.evil.example/p/abc",
        "https://notgoogleusercontent.com/p/abc",
        "data:image/png;base64,googleusercontent.com",
        "",
        None,
    ],
)
def test_rejects_other_hosts(url):
    assert not is_googleusercontent_url(url)
