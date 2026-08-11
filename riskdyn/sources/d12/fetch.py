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


class UnexpectedRedirect(Exception):
    """Raised when a request receives a 3xx response instead of content.

    D12Client never follows redirects (`follow_redirects=False`, deliberately
    kept that way): the redirect target has not passed `_check_allowed`, so
    auto-following it could silently fetch — and cache — a resource that
    robots.txt or the permission record would otherwise refuse. Nothing is
    written to the cache when this is raised.
    """

    def __init__(self, path: str, status_code: int, location: str | None) -> None:
        self.path = path
        self.status_code = status_code
        self.location = location
        super().__init__(
            f"GET {path!r} returned {status_code} redirecting to "
            f"{location!r}; D12Client does not follow redirects because the "
            f"target has not passed the robots/permission gate."
        )


def _union_robots(base: RobotsPolicy, additional: RobotsPolicy) -> RobotsPolicy:
    """Combine two robots policies, keeping the union of their disallow rules.

    A safety gate must never get weaker by talking to the network: a
    malformed, truncated, or narrower robots.txt response must not be able to
    silently drop a protection that's already in effect. So merging only ever
    adds rules, never removes them.
    """
    merged = list(base.disallowed)
    for rule in additional.disallowed:
        if rule not in merged:
            merged.append(rule)
    return RobotsPolicy(tuple(merged))


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
        # Deliberate future plumbing: kept unused branch-wide until an
        # authenticated fetch is actually needed, which requires site
        # permission we don't have yet.
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
        if 300 <= response.status_code < 400:
            # follow_redirects=False is deliberate: the redirect target has
            # not passed _check_allowed. Raise before touching the cache.
            raise UnexpectedRedirect(
                path, response.status_code, response.headers.get("Location")
            )
        response.raise_for_status()
        body = response.content
        self.cache.put(url, body)
        return body

    def get_text(self, path: str, use_cache: bool = True) -> str:
        return self.get(path, use_cache=use_cache).decode("utf-8", errors="replace")

    def get_json(self, path: str, use_cache: bool = True):
        return json.loads(self.get_text(path, use_cache=use_cache))

    def refresh_robots(self) -> RobotsPolicy:
        """Re-read robots.txt from the live site and merge it into the current policy.

        Merging (not replacing) means a malformed, truncated, or narrower
        response can only ever add disallow rules, never remove one already
        in effect — the gate can get stricter from a live fetch, never
        weaker.
        """
        self.limiter.wait()
        response = self._client.get("/robots.txt")
        if 300 <= response.status_code < 400:
            # Same guard as get(): a 3xx here must not be silently parsed as
            # robots text. Union semantics mean a narrower/malformed
            # response can only make the policy stricter, never weaker, so
            # this isn't a hole today — but leaving the guard out here while
            # get() has it is an inconsistency waiting to become one.
            raise UnexpectedRedirect(
                "/robots.txt", response.status_code, response.headers.get("Location")
            )
        response.raise_for_status()
        fetched = RobotsPolicy.parse(response.text)
        self.robots = _union_robots(self.robots, fetched)
        return self.robots

    def close(self) -> None:
        self._client.close()
