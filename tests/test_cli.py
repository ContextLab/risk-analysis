import argparse
import json
import pytest
from riskdyn.cli import D12Client, main


@pytest.mark.network
def test_pull_catalog_writes_every_map(tmp_path):
    out = tmp_path / "catalog.json"
    # --cache-dir keeps this test from writing into the developer's real
    # platform cache directory. Without it, a second run would be served
    # entirely from that persistent cache and the @pytest.mark.network test
    # would pass with no network request at all.
    cache_dir = tmp_path / "cache"
    assert main(["pull-catalog", "--out", str(out), "--cache-dir", str(cache_dir)]) == 0
    catalog = json.loads(out.read_text())
    assert len(catalog) >= 70
    assert any(entry["name"] == "World Classic" for entry in catalog)


def test_unknown_command_returns_nonzero():
    assert main(["nonsense"]) != 0


def test_non_int_system_exit_code_returns_one(monkeypatch):
    # SystemExit.code can be any object, not just int, but main()'s
    # signature promises to return an int. Force parse_args to raise a
    # SystemExit carrying a non-int code and check the guard catches it.
    def fake_parse_args(self, argv=None, namespace=None):
        raise SystemExit("boom")

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", fake_parse_args)
    assert main(["pull-catalog"]) == 1


def test_pull_catalog_output_has_no_author_fields(tmp_path, monkeypatch, fixtures_dir):
    """77 third-party usernames were deliberately stripped from the frozen
    catalog fixture; pull-catalog's output must never carry an author/authors
    key for any map, live fetch or not. D12Client.get_text is monkeypatched
    to return the committed, already-scrubbed fixture so this stays a fast,
    offline check of the write path rather than a live-network test.
    """
    fixture_html = (fixtures_dir / "maps_page.html").read_text()
    monkeypatch.setattr(
        D12Client, "get_text", lambda self, path, use_cache=True: fixture_html
    )
    out = tmp_path / "catalog.json"
    cache_dir = tmp_path / "cache"
    assert main(["pull-catalog", "--out", str(out), "--cache-dir", str(cache_dir)]) == 0
    catalog = json.loads(out.read_text())
    assert len(catalog) == 77
    for entry in catalog:
        assert "author" not in entry
        assert "authors" not in entry


def test_pull_catalog_uses_isolated_cache_dir(tmp_path, monkeypatch, fixtures_dir):
    """--cache-dir must actually route through to D12Client's cache, not the
    platform default. get_text is monkeypatched (so this stays offline), but
    the *real* D12Client.__init__ still runs, so wrapping it to record
    self.cache.root proves --cache-dir was threaded all the way from argparse
    through Settings into the client that _pull_catalog constructs.
    """
    fixture_html = (fixtures_dir / "maps_page.html").read_text()
    monkeypatch.setattr(
        D12Client, "get_text", lambda self, path, use_cache=True: fixture_html
    )
    seen_cache_roots = []
    real_init = D12Client.__init__

    def recording_init(self, settings=None, session_cookie=None):
        real_init(self, settings, session_cookie)
        seen_cache_roots.append(self.cache.root)

    monkeypatch.setattr(D12Client, "__init__", recording_init)

    out = tmp_path / "catalog.json"
    cache_dir = tmp_path / "cache"
    assert main(["pull-catalog", "--out", str(out), "--cache-dir", str(cache_dir)]) == 0
    assert seen_cache_roots == [cache_dir]
