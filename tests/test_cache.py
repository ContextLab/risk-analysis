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
