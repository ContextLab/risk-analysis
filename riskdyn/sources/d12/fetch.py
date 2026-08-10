"""The only module in riskdyn that performs network I/O.

Every request passes three gates in order: robots policy (overridable only by
an explicit PermissionRecord), the cache, and the rate limiter. Keeping this
in one place means the rest of the package is trivially offline-testable.
"""
from __future__ import annotations

import json

import httpx

from riskdyn.config import Settings
from riskdyn.sources.d12.cache import ResponseCache
from riskdyn.sources.d12.ratelimit import RateLimiter
from riskdyn.sources.d12.robots import RobotsDisallowed, RobotsPolicy

BASE_URL = "https://dominating12.com"

# Captured 2026-08-10. Refreshed by `.refresh_robots()`; hardcoded so that the
# gate is active on the very first request, before any network call.
DEFAULT_ROBOTS = """User-agent: *
Disallow: /game/
Disallow: /user/
Disallow: /userlist
"""


class D12Client:
    def __init__(
        self,
        settings: Settings | None = None,
        session_cookie: str | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.robots = RobotsPolicy.parse(DEFAULT_ROBOTS)
        self.cache = ResponseCache(self.settings.cache_dir)
        interval = (
            self.settings.permission.rate_limit_seconds
            if self.settings.permission is not None
            else self.settings.rate_limit_seconds
        )
        self.limiter = RateLimiter(interval)
        # `session_cookie` is a raw Cookie *header* value, e.g.
        # "laravel_session=abc; XSRF-TOKEN=def", not a single named cookie. D12's
        # session cookie name has never been verified, so passing the header
        # through verbatim avoids guessing it. Normally None: everything phase 0-1
        # needs is reachable unauthenticated.
        headers = {"User-Agent": self.settings.user_agent}
        if session_cookie:
            headers["Cookie"] = session_cookie
        self._client = httpx.Client(
            base_url=BASE_URL,
            headers=headers,
            follow_redirects=False,
            timeout=30.0,
        )

    def _check_allowed(self, path: str) -> None:
        # NOTE: both gates canonicalize via riskdyn.paths.canonicalize_path, so
        # they agree on what `path` means. Pass BARE PATHS only ("/maps",
        # "/api/user/names?q=x"): anything carrying a host is refused outright,
        # because "//host/x" cannot be told from a path safely.
        if self.robots.is_allowed(path):
            return
        permission = self.settings.permission
        if permission is not None and permission.covers(path):
            return
        raise RobotsDisallowed(
            f"{path!r} is disallowed by robots.txt and is not covered by a "
            f"configured permission record. Obtain written permission from the "
            f"site operator and record it via PermissionRecord before fetching."
        )

    def get(self, path: str, use_cache: bool = True) -> bytes:
        self._check_allowed(path)
        url = f"{BASE_URL}{path}"
        if use_cache:
            cached = self.cache.get(url)
            if cached is not None:
                return cached
        self.limiter.wait()
        response = self._client.get(path)
        response.raise_for_status()
        body = response.content
        self.cache.put(url, body)
        return body

    def get_text(self, path: str, use_cache: bool = True) -> str:
        return self.get(path, use_cache=use_cache).decode("utf-8", errors="replace")

    def get_json(self, path: str, use_cache: bool = True):
        return json.loads(self.get_text(path, use_cache=use_cache))

    def refresh_robots(self) -> RobotsPolicy:
        """Re-read robots.txt from the live site and adopt it."""
        self.limiter.wait()
        response = self._client.get("/robots.txt")
        response.raise_for_status()
        self.robots = RobotsPolicy.parse(response.text)
        return self.robots

    def close(self) -> None:
        self._client.close()
