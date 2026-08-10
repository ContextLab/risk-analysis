"""Content-addressed on-disk response cache.

Every fetch is cached on first retrieval so that re-analysis never re-crawls.
Cache entries are keyed by the full URL hash, and sharded two levels deep to
keep directory sizes reasonable across a large corpus.
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import tempfile


class ResponseCache:
    def __init__(self, root: str | pathlib.Path) -> None:
        self.root = pathlib.Path(root)

    def path_for(self, url: str) -> pathlib.Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.root / digest[:2] / digest[2:4] / digest

    def get(self, url: str) -> bytes | None:
        """Return cached bytes for url, or None if not cached or unreadable.

        Returns None for any non-hit: missing, is a directory, or read error.
        """
        path = self.path_for(url)
        try:
            if path.is_file():
                return path.read_bytes()
        except OSError:
            pass
        return None

    def put(self, url: str, body: bytes) -> pathlib.Path:
        path = self.path_for(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: write to temp file in the same directory, then rename.
        fd, tmp_path = tempfile.mkstemp(dir=path.parent)
        try:
            os.write(fd, body)
            os.close(fd)
            os.replace(tmp_path, path)
        except Exception:
            # Clean up temp file on write failure.
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise
        return path
