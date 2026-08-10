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

from riskdyn.paths import canonicalize_path

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
        try:
            raw = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            raise ValueError(f"Malformed JSON in permission file: {e}")

        # Validate required keys
        for key in ("granted_by", "granted_on", "allowed_prefixes"):
            if key not in raw:
                raise ValueError(f"Permission file missing required key: {key}")

        # Validate allowed_prefixes
        prefixes = raw["allowed_prefixes"]
        if not isinstance(prefixes, list):
            raise ValueError("allowed_prefixes must be a list")
        if not prefixes:
            raise ValueError("allowed_prefixes cannot be empty")
        for prefix in prefixes:
            if not isinstance(prefix, str):
                raise ValueError(f"allowed_prefixes items must be strings, got {type(prefix).__name__}")
            if not prefix.startswith("/"):
                raise ValueError(f"allowed_prefixes items must start with '/', got {prefix!r}")

        # Validate rate_limit_seconds if present
        if "rate_limit_seconds" in raw:
            rate_limit = raw["rate_limit_seconds"]
            try:
                rate_limit_float = float(rate_limit)
            except (TypeError, ValueError):
                raise ValueError(f"rate_limit_seconds must be a number, got {type(rate_limit).__name__}")
            if rate_limit_float <= 0:
                raise ValueError(f"rate_limit_seconds must be positive, got {rate_limit_float}")

        return cls(
            granted_by=raw["granted_by"],
            granted_on=raw["granted_on"],
            allowed_prefixes=tuple(raw["allowed_prefixes"]),
            rate_limit_seconds=float(raw.get("rate_limit_seconds", DEFAULT_RATE_LIMIT_SECONDS)),
            notes=raw.get("notes", ""),
        )

    def covers(self, path: str) -> bool:
        canonical = canonicalize_path(path)
        if canonical is None:
            return False
        return any(canonical.startswith(prefix) for prefix in self.allowed_prefixes)


@dataclass
class Settings:
    user_agent: str = USER_AGENT
    cache_dir: pathlib.Path = field(
        default_factory=lambda: pathlib.Path(user_cache_dir("riskdyn"))
    )
    rate_limit_seconds: float = DEFAULT_RATE_LIMIT_SECONDS
    permission: PermissionRecord | None = None
