"""Runtime settings and the data-use permission record.

The permission record is the *only* thing that unlocks robots-disallowed paths.
It exists as an explicit, reviewable artifact so that what was granted is
recorded rather than remembered.
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field

from platformdirs import user_cache_dir

USER_AGENT = "riskdyn/0.1 (academic research; jeremy.r.manning@dartmouth.edu)"
DEFAULT_RATE_LIMIT_SECONDS = 3.0


@dataclass(frozen=True)
class PermissionRecord:
    """A written data-use grant from the site operator."""

    granted_by: str
    granted_on: str
    allowed_prefixes: tuple[str, ...]
    rate_limit_seconds: float = DEFAULT_RATE_LIMIT_SECONDS
    notes: str = ""

    @classmethod
    def load(cls, path: str | pathlib.Path) -> "PermissionRecord | None":
        path = pathlib.Path(path)
        if not path.exists():
            return None
        raw = json.loads(path.read_text())
        return cls(
            granted_by=raw["granted_by"],
            granted_on=raw["granted_on"],
            allowed_prefixes=tuple(raw["allowed_prefixes"]),
            rate_limit_seconds=float(raw.get("rate_limit_seconds", DEFAULT_RATE_LIMIT_SECONDS)),
            notes=raw.get("notes", ""),
        )

    def covers(self, path: str) -> bool:
        return any(path.startswith(prefix) for prefix in self.allowed_prefixes)


@dataclass
class Settings:
    user_agent: str = USER_AGENT
    cache_dir: pathlib.Path = field(
        default_factory=lambda: pathlib.Path(user_cache_dir("riskdyn"))
    )
    rate_limit_seconds: float = DEFAULT_RATE_LIMIT_SECONDS
    permission: PermissionRecord | None = None
