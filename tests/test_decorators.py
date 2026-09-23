"""Tests for app.core.decorators.retry."""

import pytest

from app.core.decorators import retry


def test_retry_returns_after_transient_failures():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("transient")
        return "ok"

    assert retry(flaky, max_retries=3, retry_delay=0)() == "ok"
    assert len(calls) == 3


def test_retry_reraises_the_last_error():
    def broken():
        raise RuntimeError("still broken")

    with pytest.raises(RuntimeError, match="still broken"):
        retry(broken, max_retries=2, retry_delay=0)()


def test_negative_max_retries_is_a_clear_error_not_raise_none():
    # The loop never runs, so there is no exception to re-raise. This used to
    # `raise None`, which surfaces as an unrelated TypeError.
    with pytest.raises(ValueError, match="max_retries must be >= 0"):
        retry(lambda: "never called", max_retries=-1)()
