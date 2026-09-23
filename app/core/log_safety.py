"""
Defence against log injection (log forging).

Caller-supplied values reach log messages all over this codebase --
``place_id``, search queries, URLs, job ids. A newline in one of those lets an
attacker append a fabricated line to the log::

    place_id = "abc\\nINFO:root:admin login succeeded from 10.0.0.1"

Anything parsing those logs -- an aggregator, an alert rule, a human reading
an incident timeline -- then sees an event that never happened. On a service
whose entire input surface is attacker-chosen strings, that is worth closing
properly.

Two layers, deliberately:

1. :func:`scrub` at the call site. Explicit, visible in review, and
   recognisable to static analysis as a sanitiser.
2. :class:`LogInjectionFilter` on the root logger. A blanket net for every
   site nobody remembered to wrap -- including future ones and third-party
   libraries logging values we handed them.

The filter alone would be enough for safety, but not for intent: a reader of
the call site cannot see that the value is untrusted. The call-site scrub
alone would be enough for the sites we know about, but nothing stops the next
one being added unwrapped. Together they cover both.
"""

from __future__ import annotations

import logging

__all__ = ["LogInjectionFilter", "install_log_injection_filter", "scrub"]

#: Longest a single scrubbed value may be. A caller can otherwise push
#: megabytes into the log with one request.
MAX_VALUE_LENGTH = 256

# Newlines end a log record; other C0 controls corrupt terminals and confuse
# parsers. Tab is kept -- it is legitimate whitespace and not a record
# separator.
_TRANSLATION = {c: "\\x%02x" % c for c in range(0x20) if c != 0x09}
_TRANSLATION[0x7F] = "\\x7f"  # DEL


def scrub(value: object, *, max_length: int = MAX_VALUE_LENGTH) -> str:
    """Render an untrusted value safe to interpolate into a log message.

    Escapes newlines, carriage returns and other control characters, and
    truncates. Wrap any caller-supplied value passed to a logger.

    Args:
        value: The untrusted value. Converted with ``str()``.
        max_length: Maximum length of the result before truncation.

    Returns:
        A single-line string with control characters escaped.
    """
    text = str(value)
    if len(text) > max_length:
        text = text[:max_length] + "…(truncated)"
    return text.translate(_TRANSLATION)


class LogInjectionFilter(logging.Filter):
    """Strip record separators from the fully formatted log message.

    Applied after formatting, so it catches values interpolated by call sites
    that forgot :func:`scrub`, and values logged by third-party libraries.

    It intentionally does NOT truncate: a legitimate multi-line record (a
    traceback, say) must survive. Only the control characters that let one
    record masquerade as several are escaped.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - never break logging itself
            return True

        if "\n" in message or "\r" in message:
            # Tracebacks arrive via exc_info/exc_text, not the message, so
            # escaping newlines here does not damage them.
            record.msg = message.replace("\r", "\\r").replace("\n", "\\n")
            record.args = ()
        return True


def install_log_injection_filter(logger: logging.Logger | None = None) -> None:
    """Attach :class:`LogInjectionFilter` to a logger's handlers.

    Args:
        logger: Target logger; the root logger when omitted.
    """
    target = logger or logging.getLogger()
    for handler in target.handlers:
        if not any(isinstance(f, LogInjectionFilter) for f in handler.filters):
            handler.addFilter(LogInjectionFilter())
