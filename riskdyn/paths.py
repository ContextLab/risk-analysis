"""Path canonicalization for consistent validation across gates.

Path matching is case-sensitive per RFC 9309; this implements standard
robots.txt behavior and should not be overridden.
"""
from __future__ import annotations

import posixpath
import re
from urllib.parse import unquote, urlsplit


def canonicalize_path(path: str) -> str | None:
    """Canonicalize a path for comparison.

    Returns the canonical path, or None when the input is ambiguous or hostile:
    - Any input with a netloc (authority component) is refused
    - Percent-decoding that doesn't stabilize within 5 iterations is refused

    This ensures consistent semantics across all path-matching gates.
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
            return None

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
            return None

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

    return candidate
