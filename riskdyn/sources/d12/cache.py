"""Content-addressed on-disk response cache.

Every fetch is cached on first retrieval so that re-analysis never re-crawls.
Cache entries are keyed by the full URL hash, and sharded two levels deep to
keep directory sizes reasonable across a large corpus.
"""
from __future__ import annotations

import hashlib
import pathlib


class ResponseCache:
    def __init__(self, root: str | pathlib.Path) -> None:
        self.root = pathlib.Path(root)

    def path_for(self, url: str) -> pathlib.Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.root / digest[:2] / digest[2:4] / digest

    def get(self, url: str) -> bytes | None:
        path = self.path_for(url)
        return path.read_bytes() if path.exists() else None

    def put(self, url: str, body: bytes) -> pathlib.Path:
        path = self.path_for(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return path
