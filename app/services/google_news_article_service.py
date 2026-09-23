"""
Support for the /article-details/ endpoint.

Two concerns that exist only for that endpoint: the optional nltk setup that
article.nlp() needs, and the outbound fetch policy (allow-list, redirect
re-validation, body cap) that keeps a caller-supplied URL from becoming an
SSRF sink.

Moved verbatim out of app.api.google_news.google_news_api, which keeps the
route handler and still re-exports every public name defined here.
"""
import asyncio
import logging
import os

import httpx

from app.core.constants import USER_AGENTS
from app.core.proxy import get_proxy
from app.core.url_guard import NEWS_ALLOWED_HOSTS, UrlNotAllowed, validate_outbound_url

# nltk is OPTIONAL and is deliberately not in requirements.txt.
#
# nltk 3.10.3 carries PYSEC-2026-3740 / GHSA-8mgp-746c-j5xp (path traversal in
# the model-artifact APIs) and 3.10.3 is the newest release, so there is nothing
# to upgrade to; the fix is open and unmerged at nltk/nltk#3753. Rather than
# ship a known-vulnerable package, the dependency is dropped.
#
# It is only ever needed for article.nlp(), which populates `summary` and
# `keywords`. newspaper4k imports nltk lazily inside split_sentences(), so
# parsing, text, authors, dates and images all work without it. When a fixed
# nltk is released, re-add the pin to requirements.txt and both fields come back
# with no code change: the paths below already handle either state.
try:  # pragma: no cover - presence depends on the environment
    import nltk
except ImportError:  # nltk not installed: NLP features degrade, nothing else
    nltk = None

from app.services.google_news_service import get_gnews_http_client, settings

logger = logging.getLogger(__name__)

# Initialize NLTK asynchronously at module level
async def setup_nltk():
    """Setup NLTK resources once at startup, if nltk is installed at all."""
    if nltk is None:
        logger.info(
            "nltk is not installed; article summary and keywords are disabled. "
            "Everything else in /article-details/ is unaffected."
        )
        return
    try:
        # Set NLTK data path to a writable directory
        nltk_data_dir = os.path.join(os.getcwd(), "nltk_data")
        os.makedirs(nltk_data_dir, exist_ok=True)
        nltk.data.path.insert(0, nltk_data_dir)

        # Check if 'punkt_tab' is already downloaded
        try:
            nltk.data.find('tokenizers/punkt_tab')
            logger.info("NLTK 'punkt_tab' resource already available.")
        except LookupError:
            # 'punkt_tab' not found, so download it
            logger.info("NLTK 'punkt_tab' resource not found. Downloading...")
            nltk.download('punkt_tab', nltk_data_dir, quiet=True)
            logger.info("NLTK 'punkt_tab' resource downloaded successfully.")

        # Also download 'punkt' as fallback
        try:
            nltk.data.find('tokenizers/punkt')
        except LookupError:
            logger.info("Downloading fallback 'punkt' resource...")
            nltk.download('punkt', nltk_data_dir, quiet=True)

    except Exception as e:
        # Handle any other exceptions during NLTK setup
        logger.error(f"An error occurred during NLTK setup: {e}")

# Run NLTK setup at import time (this will be awaited in the lifespan event)
_nltk_setup_task = None

async def ensure_nltk_setup():
    """Ensure NLTK is set up, running setup only once."""
    global _nltk_setup_task
    if _nltk_setup_task is None:
        _nltk_setup_task = asyncio.create_task(setup_nltk())
    await _nltk_setup_task


# -----------------------------------------------------------------------------
# Outbound fetch policy for /article-details/
#
# This endpoint takes a URL from the caller and fetches it from inside the
# container network, which is a textbook SSRF sink. Everything it is allowed
# to reach is listed here, and the list is deliberately empty of wildcards:
# an allow-list that has to be widened on purpose beats a deny-list that has
# to enumerate every internal range correctly, forever.
#
# Operators extend it with NEWS_ARTICLE_ALLOWED_HOSTS, a comma-separated list
# of publisher hosts (a leading dot matches subdomains, e.g. ".bbc.co.uk").
# Until they do, only Google News itself is reachable.
# -----------------------------------------------------------------------------
def _configured_article_hosts() -> tuple[str, ...]:
    """Read the operator-supplied publisher allow-list from the environment."""
    raw = os.environ.get("NEWS_ARTICLE_ALLOWED_HOSTS", "")
    return tuple(entry.strip().lower() for entry in raw.split(",") if entry.strip())


ARTICLE_DETAILS_ALLOWED_HOSTS: tuple[str, ...] = (
    NEWS_ALLOWED_HOSTS + _configured_article_hosts()
)

# Plaintext HTTP is off by default: a downgraded fetch is both interceptable
# and a convenient way to reach internal services that never speak TLS.
ARTICLE_DETAILS_ALLOW_HTTP: bool = (
    os.environ.get("NEWS_ARTICLE_ALLOW_HTTP", "").strip().lower() in {"1", "true", "yes"}
)

# Resolve and check every address the host maps to. Only unit tests that must
# not touch the network set this to False.
ARTICLE_DETAILS_RESOLVE_DNS: bool = True

# One message for every rejection. "Connection refused" vs "timed out" vs
# "not on the allow-list" is exactly what turned this endpoint into an
# internal port-scan oracle; identical bodies remove that signal.
BLOCKED_URL_DETAIL = "The supplied URL is not permitted."
ARTICLE_FETCH_FAILED_DETAIL = "Could not retrieve the requested article."


# A redirect chain is bounded: each hop is another outbound request, and an
# unbounded chain is a cheap way to keep a worker busy.
ARTICLE_MAX_REDIRECTS = 3

# Cap on the decompressed article body. Generous for a news page, and small
# enough that one request cannot exhaust the worker's memory.
ARTICLE_MAX_BYTES = 8 * 1024 * 1024


def validate_article_url(raw_url: str):
    """Run a URL through the shared guard with this endpoint's policy."""
    return validate_outbound_url(
        raw_url,
        allowed_hosts=ARTICLE_DETAILS_ALLOWED_HOSTS,
        allow_http=ARTICLE_DETAILS_ALLOW_HTTP,
        resolve_dns=ARTICLE_DETAILS_RESOLVE_DNS,
    )


async def fetch_allow_listed_html(validated_url: str) -> tuple[str, str]:
    """Fetch ``validated_url``, re-validating every redirect it is sent on.

    Validating once and then handing the URL to a library that follows
    redirects checks only the first hop: an allow-listed host that answers
    ``302 Location: http://169.254.169.254/`` sends the *library's* request
    somewhere the guard never saw. Redirects are therefore not followed
    automatically; each ``Location`` goes back through the same validation as
    the caller's original URL.

    The body is streamed and capped at ``ARTICLE_MAX_BYTES``. Reading
    ``response.text`` in one go would buffer whatever the far end sends, and
    the cap has to be applied to the *decompressed* stream: a few kilobytes of
    gzip can expand to gigabytes, so a Content-Length check is not enough.

    Returns:
        ``(html, final_url)`` -- the body, and the URL it actually came from.

    Raises:
        UrlNotAllowed: if any hop fails validation, or the body is too large.
        httpx.HTTPError: on a transport or status failure.

    Known residual risk: between validation and connect, a hostile DNS server
    could swap a public answer for a private one (rebinding). Closing that
    needs connection-level pinning to ``ValidatedUrl.ip_addresses``, which
    httpx cannot express without a custom transport. The window is narrow
    here because the host must already be on an operator-managed allow-list.
    A configured outbound proxy resolves the host itself, which makes the
    guard's DNS check advisory on that path; the scheme, host and port checks
    still apply.
    """
    proxy_url = await get_proxy()
    client = await get_gnews_http_client(proxy_url=proxy_url)

    current = validated_url
    for _ in range(ARTICLE_MAX_REDIRECTS + 1):
        async with client.stream(
            "GET",
            current,
            follow_redirects=False,
            timeout=settings.HTTP_READ_TIMEOUT,
            headers={"User-Agent": USER_AGENTS["windows_chrome"]},
        ) as response:
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise UrlNotAllowed("redirect without a Location header")

                # Relative redirects resolve against the current URL, so
                # validating the joined result means a relative hop cannot
                # smuggle in a new host. A hop to another scheme is validated
                # like any other: only https (http if explicitly enabled).
                next_url = str(httpx.URL(current).join(location))
                current = validate_article_url(next_url).url
                continue

            response.raise_for_status()

            chunks = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > ARTICLE_MAX_BYTES:
                    raise UrlNotAllowed(
                        f"response body exceeded {ARTICLE_MAX_BYTES} bytes"
                    )
                chunks.append(chunk)

            encoding = response.charset_encoding or "utf-8"
            return b"".join(chunks).decode(encoding, errors="replace"), current

    raise UrlNotAllowed(f"redirect chain exceeded {ARTICLE_MAX_REDIRECTS} hops")
