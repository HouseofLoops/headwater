"""Tests for log-injection defence.

A caller-supplied value containing a newline lets an attacker append a
fabricated line to the log:

    place_id = "abc\\nINFO:root:admin login succeeded from 10.0.0.1"

Anything reading those logs -- an aggregator, an alert rule, a human building
an incident timeline -- then sees an event that never happened.
"""

import logging

import pytest

from app.core.log_safety import (
    MAX_VALUE_LENGTH,
    LogInjectionFilter,
    install_log_injection_filter,
    scrub,
)

FORGED = "abc\nINFO:root:admin login succeeded from 10.0.0.1"


class TestScrub:
    """Layer 1: explicit sanitisation at the call site."""

    def test_newline_cannot_start_a_new_record(self):
        out = scrub(FORGED)
        assert "\n" not in out
        assert "\\x0a" in out

    def test_carriage_return_is_escaped(self):
        assert "\r" not in scrub("a\rb")

    @pytest.mark.parametrize("ch", ["\x00", "\x07", "\x1b", "\x7f"])
    def test_control_characters_are_escaped(self, ch):
        # NUL truncates C-side consumers; ESC can drive a terminal; DEL corrupts.
        assert ch not in scrub(f"a{ch}b")

    def test_tab_is_preserved(self):
        # Legitimate whitespace, and not a record separator.
        assert "\t" in scrub("a\tb")

    def test_ordinary_text_is_unchanged(self):
        assert scrub("ChIJN1t_tDeuEmsRUsoyG83frY4") == "ChIJN1t_tDeuEmsRUsoyG83frY4"

    def test_long_values_are_truncated(self):
        out = scrub("x" * (MAX_VALUE_LENGTH * 3))
        assert len(out) < MAX_VALUE_LENGTH * 2
        assert out.endswith("(truncated)")

    def test_truncation_limit_is_configurable(self):
        assert scrub("x" * 100, max_length=10).startswith("x" * 10)

    def test_non_string_values_are_accepted(self):
        assert scrub(42) == "42"
        assert scrub(None) == "None"

    def test_unicode_survives(self):
        assert scrub("café 日本") == "café 日本"


class TestLogInjectionFilter:
    """Layer 2: the blanket net for sites nobody wrapped."""

    def _record(self, msg, args=()):
        return logging.LogRecord(
            name="t",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg=msg,
            args=args,
            exc_info=None,
        )

    def test_newline_in_an_unwrapped_message_is_escaped(self):
        record = self._record("Place lookup: %s", (FORGED,))
        LogInjectionFilter().filter(record)
        assert "\n" not in record.getMessage()

    def test_filter_always_lets_the_record_through(self):
        # It sanitises; it must never silently drop a log line.
        record = self._record("anything")
        assert LogInjectionFilter().filter(record) is True

    def test_clean_messages_are_untouched(self):
        record = self._record("Place lookup: %s", ("ChIJabc",))
        LogInjectionFilter().filter(record)
        assert record.getMessage() == "Place lookup: ChIJabc"

    def test_a_broken_message_does_not_break_logging(self):
        # Too few args for the format string: getMessage() raises. The filter
        # must not turn a logging bug into a request failure.
        record = self._record("%s %s", ("only-one",))
        assert LogInjectionFilter().filter(record) is True


class TestInstall:
    def test_filter_is_attached_to_handlers(self):
        logger = logging.getLogger("test-install-lf")
        logger.handlers = [logging.NullHandler()]
        install_log_injection_filter(logger)
        assert any(isinstance(f, LogInjectionFilter) for f in logger.handlers[0].filters)

    def test_installing_twice_does_not_duplicate(self):
        logger = logging.getLogger("test-install-twice")
        logger.handlers = [logging.NullHandler()]
        install_log_injection_filter(logger)
        install_log_injection_filter(logger)
        count = sum(isinstance(f, LogInjectionFilter) for f in logger.handlers[0].filters)
        assert count == 1


class TestEndToEnd:
    def test_a_forged_place_id_cannot_inject_a_log_line(self, caplog):
        """The scenario, through a real logger."""
        logger = logging.getLogger("test-e2e-forge")
        with caplog.at_level(logging.INFO, logger="test-e2e-forge"):
            logger.info("Place lookup by ID: %s", scrub(FORGED))

        assert len(caplog.records) == 1
        assert "\n" not in caplog.records[0].getMessage()
