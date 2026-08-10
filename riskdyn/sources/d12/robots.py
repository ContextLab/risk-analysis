"""robots.txt parsing and path gating for dominating12.com.

Only the ``User-agent: *`` group is honored, which is the only group D12
publishes. Directives are prefix matches, per the robots.txt convention.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


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
        """True if ``path`` (a path, or a full URL) may be fetched."""
        candidate = urlsplit(path).path or "/"
        return not any(candidate.startswith(rule) for rule in self.disallowed)
