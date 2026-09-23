"""Tests for the outbound URL validator (SSRF defence).

These cover the bypasses that matter, not just the happy path: the whole point
of the module is that a naive host check is insufficient.
"""

from unittest.mock import patch

import pytest

from app.core.url_guard import (
    MAPS_ALLOWED_HOSTS,
    NEWS_ALLOWED_HOSTS,
    UrlNotAllowed,
    validate_outbound_url,
)

NEWS = NEWS_ALLOWED_HOSTS
MAPS = MAPS_ALLOWED_HOSTS


def _validate(url, hosts=NEWS, **kw):
    """Validate without touching DNS unless a test explicitly wants to."""
    kw.setdefault("resolve_dns", False)
    return validate_outbound_url(url, allowed_hosts=hosts, **kw)


class TestAllowedUrls:
    def test_exact_host_is_accepted(self):
        result = _validate("https://news.google.com/rss/articles/abc123")
        assert result.host == "news.google.com"
        assert result.port == 443

    def test_subdomain_accepted_when_entry_has_leading_dot(self):
        assert _validate("https://foo.news.google.com/x").host == "foo.news.google.com"

    def test_maps_host_accepted(self):
        assert _validate("https://www.google.com/maps/place/x", MAPS).host == "www.google.com"

    def test_trailing_dot_host_is_normalised(self):
        # "google.com." and "google.com" are the same name to a resolver.
        assert _validate("https://news.google.com./x").host == "news.google.com"

    def test_uppercase_host_is_normalised(self):
        assert _validate("https://NEWS.GOOGLE.COM/x").host == "news.google.com"

    def test_fragment_is_stripped(self):
        assert "#" not in _validate("https://news.google.com/x#frag").url

    def test_query_string_is_preserved(self):
        assert _validate("https://news.google.com/x?a=1&b=2").url.endswith("?a=1&b=2")


class TestSchemeRejection:
    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "gopher://news.google.com/",
            "ftp://news.google.com/",
            "dict://news.google.com:11211/",
            "//news.google.com/x",  # scheme-relative: resolves against our host
            "/etc/passwd",  # bare path
        ],
    )
    def test_non_http_schemes_rejected(self, url):
        with pytest.raises(UrlNotAllowed):
            _validate(url)

    def test_plain_http_rejected_by_default(self):
        with pytest.raises(UrlNotAllowed, match="scheme"):
            _validate("http://news.google.com/x")

    def test_plain_http_allowed_when_opted_in(self):
        assert _validate("http://news.google.com/x", allow_http=True).port == 80


class TestHostRejection:
    @pytest.mark.parametrize(
        "url",
        [
            "https://evil.com/x",
            "https://localhost/x",
            "https://metadata.google.internal/x",
            # Suffix confusion: must not match by naive `in` or endswith on a
            # dotless entry.
            "https://news.google.com.attacker.tld/x",
            "https://evilnews.google.com/x",
            "https://notgoogle.com/maps/",
        ],
    )
    def test_hosts_off_allow_list_rejected(self, url):
        with pytest.raises(UrlNotAllowed, match="allow-list"):
            _validate(url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://127.0.0.1/x",
            "https://169.254.169.254/latest/meta-data/",
            "https://10.0.0.1/x",
            "https://[::1]/x",
        ],
    )
    def test_literal_ips_rejected(self, url):
        with pytest.raises(UrlNotAllowed, match="literal IP"):
            _validate(url)

    def test_credentials_in_url_rejected(self):
        # Disguises the real host from humans and from naive parsers.
        with pytest.raises(UrlNotAllowed, match="credentials"):
            _validate("https://news.google.com@evil.com/x")

    def test_empty_url_rejected(self):
        with pytest.raises(UrlNotAllowed, match="empty"):
            _validate("   ")

    def test_empty_allow_list_fails_closed(self):
        # An empty allow-list means "fetch anything" -- the original bug.
        with pytest.raises(UrlNotAllowed, match="no allowed_hosts"):
            _validate("https://news.google.com/x", hosts=())


class TestPortRejection:
    @pytest.mark.parametrize("port", [6379, 5432, 9200, 8080, 22])
    def test_internal_service_ports_rejected(self, port):
        with pytest.raises(UrlNotAllowed, match="port"):
            _validate(f"https://news.google.com:{port}/x")

    def test_explicit_default_port_accepted(self):
        assert _validate("https://news.google.com:443/x").port == 443


class TestDnsResolution:
    """The layer a host-only check misses: allow-listed name, hostile answer."""

    def _with_resolved(self, addresses):
        return patch(
            "app.core.url_guard.socket.getaddrinfo",
            return_value=[(None, None, None, "", (ip, 443)) for ip in addresses],
        )

    def test_public_address_accepted(self):
        with self._with_resolved(["142.250.72.238"]):
            result = validate_outbound_url("https://news.google.com/x", allowed_hosts=NEWS)
        assert result.ip_addresses == ("142.250.72.238",)

    @pytest.mark.parametrize(
        "ip",
        [
            "127.0.0.1",  # loopback
            "10.1.2.3",  # RFC 1918
            "192.168.1.1",  # RFC 1918
            "172.16.0.1",  # RFC 1918
            "169.254.169.254",  # cloud metadata (link-local)
            "::1",  # IPv6 loopback
            "fd00::1",  # IPv6 unique-local
            "::ffff:127.0.0.1",  # IPv4-mapped loopback
            "0.0.0.0",  # unspecified
            # Ranges an enumerate-the-bad-ones check misses:
            "100.64.0.1",  # CGNAT (RFC 6598) -- routable inside ISP/cloud nets
            "100.127.255.254",  # CGNAT upper bound
            "192.0.0.1",  # IETF protocol assignments
            "198.18.0.1",  # benchmarking range
            "240.0.0.1",  # reserved 240.0.0.0/4
            # IPv6 transition forms carrying an internal IPv4 destination:
            "64:ff9b::7f00:1",  # NAT64 -> 127.0.0.1
            "64:ff9b::a9fe:a9fe",  # NAT64 -> 169.254.169.254 (metadata)
            "2002:7f00:1::",  # 6to4 -> 127.0.0.1
            "2002:a00:1::",  # 6to4 -> 10.0.0.1
        ],
    )
    def test_non_routable_answers_rejected(self, ip):
        # An allow-listed name that resolves inward is a hijacked or
        # split-horizon record. Refuse regardless of the name.
        with self._with_resolved([ip]):
            with pytest.raises(UrlNotAllowed, match="non-routable"):
                validate_outbound_url("https://news.google.com/x", allowed_hosts=NEWS)

    def test_rejected_when_any_answer_is_private(self):
        # Mixed answers: a rebinding record often returns both.
        with self._with_resolved(["142.250.72.238", "127.0.0.1"]):
            with pytest.raises(UrlNotAllowed, match="non-routable"):
                validate_outbound_url("https://news.google.com/x", allowed_hosts=NEWS)

    def test_resolution_failure_rejected(self):
        import socket as _socket

        with patch(
            "app.core.url_guard.socket.getaddrinfo",
            side_effect=_socket.gaierror("nope"),
        ):
            with pytest.raises(UrlNotAllowed, match="DNS resolution failed"):
                validate_outbound_url("https://news.google.com/x", allowed_hosts=NEWS)

    def test_addresses_returned_for_pinning(self):
        # Callers pin to these to close the DNS-rebinding window between
        # validation and the actual connection.
        with self._with_resolved(["142.250.72.238", "142.250.72.239"]):
            result = validate_outbound_url("https://news.google.com/x", allowed_hosts=NEWS)
        assert len(result.ip_addresses) == 2


class TestErrorMessagesAreNotAnOracle:
    """The public message must not distinguish failure causes."""

    @pytest.mark.parametrize(
        "url",
        [
            "https://evil.com/x",
            "https://news.google.com:6379/x",
            "file:///etc/passwd",
            "https://127.0.0.1/x",
        ],
    )
    def test_public_message_is_identical_for_every_cause(self, url):
        with pytest.raises(UrlNotAllowed) as exc:
            _validate(url)
        assert exc.value.public_message == "The supplied URL is not permitted."

    def test_reason_is_detailed_for_logs(self):
        # The detailed reason still exists -- for server-side logging only.
        with pytest.raises(UrlNotAllowed) as exc:
            _validate("https://evil.com/x")
        assert "evil.com" in exc.value.reason


class TestGloballyRoutableHelper:
    """Direct coverage of the address classifier.

    Regression guard: the first implementation enumerated private/loopback/
    link-local/multicast/reserved/unspecified and therefore admitted CGNAT.
    """

    @pytest.mark.parametrize(
        "ip",
        [
            "8.8.8.8",
            "142.250.72.238",
            "2001:4860:4860::8888",
            "64:ff9b::8.8.8.8",  # NAT64 to a genuinely public IPv4 is fine
        ],
    )
    def test_public_addresses_allowed(self, ip):
        from app.core.url_guard import _is_globally_routable

        assert _is_globally_routable(ip) is True

    @pytest.mark.parametrize(
        "ip",
        [
            "100.64.0.1",
            "192.0.0.1",
            "198.18.0.1",
            "240.0.0.1",
            "64:ff9b::7f00:1",
            "2002:7f00:1::",
            "127.0.0.1",
            "169.254.169.254",
            "not-an-ip",
        ],
    )
    def test_non_routable_addresses_blocked(self, ip):
        from app.core.url_guard import _is_globally_routable

        assert _is_globally_routable(ip) is False
