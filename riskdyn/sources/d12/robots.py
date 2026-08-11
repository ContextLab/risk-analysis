"""robots.txt parsing and path gating for dominating12.com.

Only the ``User-agent: *`` group is honored, which is the only group D12
publishes. Directives are prefix matches, per the robots.txt convention.

Path matching is case-sensitive per RFC 9309; this implements standard
robots.txt behavior and should not be overridden.
"""
from __future__ import annotations

from dataclasses import dataclass

from riskdyn.paths import canonicalize_path


class RobotsDisallowed(Exception):
    """Raised when a fetch targets a path robots.txt disallows."""


@dataclass(frozen=True)
class RobotsPolicy:
    disallowed: tuple[str, ...]

    @classmethod
    def parse(cls, text: str) -> "RobotsPolicy":
        rules: list[str] = []
        in_star_group = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            field, value = (part.strip() for part in line.split(":", 1))
            field = field.lower()
            if field == "user-agent":
                in_star_group = value == "*"
            elif field == "disallow" and in_star_group and value:
                rules.append(value)
        return cls(tuple(rules))

    def is_allowed(self, path: str) -> bool:
        """True if ``path`` (a bare path optionally with query/fragment) may be fetched.

        Refuses any input with an authority component (netloc): the `//host/path` form
        is genuinely ambiguous between protocol-relative URLs and bare paths starting
        with //, and this gate cannot disambiguate safely, so it errs closed.
        """
        canonical = canonicalize_path(path)
        if canonical is None:
            return False
        # posixpath.normpath (inside canonicalize_path) strips a trailing
        # slash, so a directory-root request like "/game/" canonicalizes to
        # "/game", and "/game".startswith("/game/") is False — a bypass of
        # the "/game/" rule. Matching against the canonical path AND the
        # canonical path with a trailing slash appended closes that hole
        # without touching canonicalization itself (other code depends on
        # normpath's stripping behavior).
        canonical_slash = canonical + "/"
        return not any(
            canonical.startswith(rule) or canonical_slash.startswith(rule)
            for rule in self.disallowed
        )
