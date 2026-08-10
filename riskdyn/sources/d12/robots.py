"""robots.txt parsing and path gating for dominating12.com.

Only the ``User-agent: *`` group is honored, which is the only group D12
publishes. Directives are prefix matches, per the robots.txt convention.

Path matching is case-sensitive per RFC 9309; this implements standard
robots.txt behavior and should not be overridden.
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit


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
        # Handle empty string as "/" (canonicalize root)
        if not path:
            candidate = "/"
        else:
            # Parse to check for netloc (authority component)
            parts = urlsplit(path)

            # Refuse any input with a netloc: includes //host/path, //evil.example/...
            # and https://host/path. This is fail-closed and correct for this gate's use case.
            if parts.netloc:
                return False

            # Extract path component, defaulting to "/" for empty paths
            candidate = parts.path or "/"

            # Percent-decode repeatedly (max 5 iterations) to foil nested encoding attacks
            # If still changing after 5 iterations, treat as hostile and block
            for attempt in range(5):
                decoded = unquote(candidate)
                if decoded == candidate:
                    # Decoding stopped making changes; we're done
                    break
                candidate = decoded
            else:
                # Loop completed without breaking (still changing after 5 iterations)
                return False

            # Collapse all slash runs to single slash (leading and interior)
            candidate = re.sub(r'/+', '/', candidate)

            # Resolve dot-segments (.. and .)
            candidate = posixpath.normpath(candidate)

            # normpath collapses "" to "." and removes trailing slashes
            # Ensure path starts with "/" (normpath may remove it)
            if not candidate.startswith("/"):
                candidate = "/" + candidate
            # If result is ".", it means root was requested; treat as "/"
            if candidate == ".":
                candidate = "/"
        return not any(candidate.startswith(rule) for rule in self.disallowed)
