import os
import pathlib
import stat

import pytest

from riskdyn.sources.d12.cache import ResponseCache


def test_miss_returns_none(tmp_path):
    assert ResponseCache(tmp_path).get("https://dominating12.com/maps") is None


def test_roundtrip_returns_exact_bytes(tmp_path):
    cache = ResponseCache(tmp_path)
    body = b"\x00\x01binary\xff payload"
    cache.put("https://dominating12.com/maps", body)
    assert cache.get("https://dominating12.com/maps") == body


def test_distinct_urls_do_not_collide(tmp_path):
    cache = ResponseCache(tmp_path)
    cache.put("https://dominating12.com/maps", b"a")
    cache.put("https://dominating12.com/maps?page=2", b"b")
    assert cache.get("https://dominating12.com/maps") == b"a"
    assert cache.get("https://dominating12.com/maps?page=2") == b"b"


def test_cache_survives_a_new_instance(tmp_path):
    ResponseCache(tmp_path).put("https://dominating12.com/maps", b"persisted")
    assert ResponseCache(tmp_path).get("https://dominating12.com/maps") == b"persisted"


def test_no_temp_files_remain_after_put(tmp_path):
    """After put, only the expected entry exists; no temporary files."""
    cache = ResponseCache(tmp_path)
    cache.put("https://dominating12.com/maps", b"content")
    # Walk entire cache tree and collect all files.
    files = list(tmp_path.rglob("*"))
    regular_files = [f for f in files if f.is_file()]
    # Should have exactly one regular file (the cache entry).
    assert len(regular_files) == 1
    assert regular_files[0] == cache.path_for("https://dominating12.com/maps")


def test_get_returns_none_when_path_is_directory(tmp_path):
    """get() returns None if the cache path exists but is a directory."""
    cache = ResponseCache(tmp_path)
    url = "https://dominating12.com/maps"
    path = cache.path_for(url)
    # Create the cache entry as a directory instead of a file.
    path.mkdir(parents=True, exist_ok=True)
    assert cache.get(url) is None


@pytest.mark.skipif(
    os.getuid() == 0 if hasattr(os, "getuid") else False,
    reason="cannot test chmod as root",
)
@pytest.mark.skipif(
    not hasattr(os, "chmod"), reason="chmod not available on this platform"
)
def test_get_returns_none_for_unreadable_file(tmp_path):
    """get() returns None if the cache file exists but is unreadable."""
    cache = ResponseCache(tmp_path)
    url = "https://dominating12.com/maps"
    path = cache.path_for(url)
    # Write the file normally.
    cache.put(url, b"content")
    # Make it unreadable.
    os.chmod(path, 0o000)
    try:
        assert cache.get(url) is None
    finally:
        # Restore permissions so cleanup works.
        os.chmod(path, 0o644)


def test_temp_files_not_mistaken_for_real_entries(tmp_path):
    """Temporary files left in cache dir are not returned as real entries."""
    cache = ResponseCache(tmp_path)
    url = "https://dominating12.com/maps"
    path = cache.path_for(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Simulate a leftover temp file in the cache directory.
    stray_temp = path.parent / ".tmpXYZ"
    stray_temp.write_bytes(b"stray")
    # get() should not return the stray temp; url should still be a miss.
    assert cache.get(url) is None
    # Now put the real entry.
    cache.put(url, b"real")
    # get() should return the real entry, not the stray.
    assert cache.get(url) == b"real"


def test_byte_exactness_with_binary_payload(tmp_path):
    """Byte-exactness still holds: round-trip binary with nulls, 0xFF, invalid UTF-8."""
    cache = ResponseCache(tmp_path)
    # Include null bytes, 0xFF, and invalid UTF-8 sequences.
    body = b"\x00\x01\x02\xfe\xff\xfd invalid utf-8 \x80\x81"
    cache.put("https://dominating12.com/data", body)
    retrieved = cache.get("https://dominating12.com/data")
    assert retrieved == body
    assert retrieved is not None
    assert len(retrieved) == len(body)
