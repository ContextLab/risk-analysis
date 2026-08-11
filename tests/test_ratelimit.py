import time
import pytest
from riskdyn.sources.d12.ratelimit import RateLimiter


def test_first_call_does_not_block():
    assert RateLimiter(0.2).wait() == 0.0


def test_subsequent_calls_are_spaced_by_min_interval():
    limiter = RateLimiter(0.2)
    start = time.monotonic()
    limiter.wait()
    limiter.wait()
    limiter.wait()
    elapsed = time.monotonic() - start
    # two enforced gaps of 0.2s; allow scheduler slack
    assert elapsed >= 0.4
    assert elapsed < 1.0


def test_no_sleep_when_enough_time_already_passed():
    limiter = RateLimiter(0.05)
    limiter.wait()
    time.sleep(0.06)
    assert limiter.wait() == 0.0


def test_negative_interval_is_rejected():
    # The module docstring says politeness is "not a tunable" — a negative
    # interval would silently disable it rather than slow it down.
    with pytest.raises(ValueError):
        RateLimiter(-0.1)


def test_zero_interval_remains_allowed():
    # Zero means "no delay" and is used deliberately in fast tests; it must
    # not be caught by the negative-interval guard.
    assert RateLimiter(0.0).wait() == 0.0
