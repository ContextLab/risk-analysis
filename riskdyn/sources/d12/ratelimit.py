"""Minimum-interval rate limiting.

Deliberately simple and always-on: politeness toward a small community site is
a correctness property here, not a tunable.
"""
from __future__ import annotations

import time


class RateLimiter:
    def __init__(self, min_interval_seconds: float) -> None:
        self.min_interval_seconds = min_interval_seconds
        self._last_call: float | None = None

    def wait(self) -> float:
        """Block until the minimum interval has elapsed. Returns seconds slept."""
        now = time.monotonic()
        if self._last_call is None:
            self._last_call = now
            return 0.0
        elapsed = now - self._last_call
        remaining = self.min_interval_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)
            self._last_call = time.monotonic()
            return remaining
        self._last_call = now
        return 0.0
