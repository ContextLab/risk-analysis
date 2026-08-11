"""Import a saved D12 game page into schema-v2 annotations (issue #4).

All tests run against the real committed fixture
``tests/fixtures/game_map1_territories.html`` (42 territories, 83
undirected edges), copied into a tmp_path to act as a saved page.  No
mocks: the importer, the shared parser, and the graph_build validator all
run for real.
"""
import json
import pathlib
import shutil
import subprocess
import sys

import pytest

from riskdyn.segment import catalog as cat
from riskdyn.workbench.graph_build import load_annotations_v2, validate_annotations
from riskdyn.workbench.import_page import import_page, status_report

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PYTHON = REPO_ROOT / ".venv" / "bin" / "python"

# Token-like content a REAL saved page carries (the committed fixture is
# scrubbed, so a page with live-looking secrets is reconstructed around it).
FAKE_SECRETS = [
    "Xq7TokenValue123SecretCsrf",
    "PHPSESSID=deadbeefcafe42",
    "sneaky_username_jjm",
    "game-4477-player-state",
]


@pytest.fixture
def saved_page(fixtures_dir, tmp_path) -> pathlib.Path:
    """The real fixture, copied to act as data/raw/saved_pages/1.html."""
    pages = tmp_path / "saved_pages"
    pages.mkdir()
    dst = pages / "1.html"
    shutil.copy(fixtures_dir / "game_map1_territories.html", dst)
    return dst


@pytest.fixture
def authored_root(tmp_path) -> pathlib.Path:
    return tmp_path / "authored"


def _read(authored_root: pathlib.Path, map_id: int) -> dict:
    return json.loads(
        (authored_root / str(map_id) / "annotations.json").read_text()
    )


def test_import_map1_counts_and_confirmed_edges(saved_page, authored_root):
    result = import_page(1, html_path=saved_page, authored_root=authored_root)
    doc = _read(authored_root, 1)
    assert doc == result.doc
    assert doc["schema_version"] == 2
    assert doc["map_id"] == 1
    assert len(doc["territories"]) == 42
    assert len(doc["edges"]) == 83
    for e in doc["edges"]:
        assert e["status"] == "confirmed"
        assert e["kind"] == "unknown"
        assert e["kind_status"] == "unconfirmed"
        assert e["source"] == "d12-markup"
        assert e["a"] < e["b"]
    # edges are undirected and unique
    pairs = {(e["a"], e["b"]) for e in doc["edges"]}
    assert len(pairs) == 83
    # the catalog matches, so no warnings
    assert result.warnings == []


def test_output_passes_graph_build_validation(saved_page, authored_root):
    """The written file is exactly what graph_build expects."""
    import_page(1, html_path=saved_page, authored_root=authored_root)
    path = authored_root / "1" / "annotations.json"
    summary = cat.load_catalog()[1]
    doc = load_annotations_v2(path, 1)  # schema/shape checks
    validate_annotations(doc, summary.width, summary.height)  # raises on any problem


def test_region_ids_empty_and_regions_empty(saved_page, authored_root):
    import_page(1, html_path=saved_page, authored_root=authored_root)
    doc = _read(authored_root, 1)
    # membership is a many-to-many LIST: [] for every imported territory,
    # never the retired scalar and never [0]
    assert all(t["region_ids"] == [] for t in doc["territories"])
    assert all("region_id" not in t for t in doc["territories"])
    assert doc["regions"] == []
    assert doc["extra_bonuses"] == []
    assert doc["special_rules"] == []
    # verification block: same shape as map 1's, nothing verified
    ver = doc["verification"]
    assert set(ver) == {"overlay_confirmed", "bonuses_confirmed"}
    for block in ver.values():
        assert block == {"verified": False, "by": None, "at": None}


def test_names_and_coordinates_match_fixture(saved_page, authored_root):
    import_page(1, html_path=saved_page, authored_root=authored_root)
    doc = _read(authored_root, 1)
    by_id = {t["territory_id"]: t for t in doc["territories"]}
    expected = {
        5: ("Northwest Territory", 92, 68),
        7: ("Ontario", 122, 126),
        31: ("Central America", 53, 277),
        59: ("Japan", 913, 196),
        62: ("Papua New Guinea", 949, 390),
        66: ("Alaska", 7, 62),
        69: ("Alberta", 53, 122),
    }
    for tid, (name, x, y) in expected.items():
        t = by_id[tid]
        assert (t["name"], t["x"], t["y"]) == (name, x, y)
        assert t["source"] == "d12-markup"
        assert t["confidence"] == "high"
    # spot-check adjacency really made it into the edge list
    pairs = {(e["a"], e["b"]) for e in doc["edges"]}
    assert {(5, 7), (5, 11), (5, 66), (5, 69)} <= pairs


def test_refuses_to_overwrite_without_force(saved_page, authored_root):
    ann = authored_root / "1" / "annotations.json"
    ann.parent.mkdir(parents=True)
    original = json.dumps({"schema_version": 2, "map_id": 1, "hand": "work"})
    ann.write_text(original)
    with pytest.raises(FileExistsError):
        import_page(1, html_path=saved_page, authored_root=authored_root)
    assert ann.read_text() == original  # nothing changed


def test_force_preserves_hand_authored_blocks(saved_page, authored_root):
    regions = [
        {
            "region_id": 1,
            "name": "North America",
            "bonus": 5,
            "territory_names": ["Alaska", "Alberta"],
        }
    ]
    extra_bonuses = [{"kind": "pair", "value": 2, "note": "hold both islands"}]
    special_rules = ["fog of war"]
    verification = {
        "overlay_confirmed": {"verified": True, "by": "jeremy", "at": "2026-08-10"},
        "bonuses_confirmed": {"verified": False, "by": None, "at": None},
    }
    existing = {
        "schema_version": 2,
        "map_id": 1,
        "territories": [
            {
                "territory_id": 5,
                "name": "Northwest Territory",
                "x": 92,
                "y": 68,
                "region_ids": [1],
                "source": "d12-markup",
                "confidence": "high",
            }
        ],
        "edges": [],
        "regions": regions,
        "extra_bonuses": extra_bonuses,
        "special_rules": special_rules,
        "verification": verification,
    }
    ann = authored_root / "1" / "annotations.json"
    ann.parent.mkdir(parents=True)
    ann.write_text(json.dumps(existing))

    result = import_page(
        1, html_path=saved_page, force=True, authored_root=authored_root
    )
    doc = _read(authored_root, 1)
    # hand-authored blocks survive verbatim
    assert doc["regions"] == regions
    assert doc["extra_bonuses"] == extra_bonuses
    assert doc["special_rules"] == special_rules
    assert doc["verification"] == verification
    # territories and edges are rebuilt from the markup
    assert len(doc["territories"]) == 42
    assert len(doc["edges"]) == 83
    # the dropped region assignment is flagged loudly
    assert any("region assignments" in w for w in result.warnings)


def test_output_contains_no_token_like_content(fixtures_dir, tmp_path):
    """A realistic saved page (site chrome + live tokens around the same
    territory markup) must never leak anything but topology fields."""
    fixture = (fixtures_dir / "game_map1_territories.html").read_text()
    page = tmp_path / "saved_pages" / "1.html"
    page.parent.mkdir()
    page.write_text(
        "<html><head>\n"
        f'<meta name="csrf-token" content="{FAKE_SECRETS[0]}">\n'
        f'<script>document.cookie = "{FAKE_SECRETS[1]}";'
        f'var user = "{FAKE_SECRETS[2]}";</script>\n'
        f'</head><body data-game="{FAKE_SECRETS[3]}">\n'
        f"{fixture}\n"
        "</body></html>\n"
    )
    authored = tmp_path / "authored"
    import_page(1, html_path=page, authored_root=authored)
    out = (authored / "1" / "annotations.json").read_text()
    for secret in FAKE_SECRETS:
        assert secret not in out
    for marker in ("<script", "<meta", "<a ", "cookie", "csrf", "href="):
        assert marker not in out.lower()
    # and it is still a full, valid import
    doc = json.loads(out)
    assert len(doc["territories"]) == 42
    assert len(doc["edges"]) == 83


def test_map_absent_from_catalog_still_imports(fixtures_dir, tmp_path):
    catalog = cat.load_catalog()
    assert 9 not in catalog  # e.g. map 9: real gap in the catalog
    page = tmp_path / "9.html"
    shutil.copy(fixtures_dir / "game_map1_territories.html", page)
    authored = tmp_path / "authored"
    result = import_page(9, html_path=page, authored_root=authored)
    doc = _read(authored, 9)
    assert doc["map_id"] == 9
    assert len(doc["territories"]) == 42
    assert any("not in the catalog" in w for w in result.warnings)


def test_catalog_count_mismatch_warns_but_writes(fixtures_dir, tmp_path):
    catalog = cat.load_catalog()
    # a real catalog map whose expected count differs from the fixture's 42
    mid = next(
        m for m in sorted(catalog) if m != 1 and catalog[m].num_territories != 42
    )
    page = tmp_path / f"{mid}.html"
    shutil.copy(fixtures_dir / "game_map1_territories.html", page)
    authored = tmp_path / "authored"
    result = import_page(mid, html_path=page, authored_root=authored)
    assert (authored / str(mid) / "annotations.json").is_file()  # still written
    assert any("MISMATCH" in w for w in result.warnings)


def test_status_report(saved_page, authored_root, tmp_path):
    saved_root = saved_page.parent
    # map 1 imported; also an out-of-catalog import (map 9)
    import_page(1, html_path=saved_page, authored_root=authored_root)
    page9 = tmp_path / "9.html"
    shutil.copy(saved_page, page9)
    import_page(9, html_path=page9, authored_root=authored_root)
    # a catalog map with a saved page but no annotations yet
    catalog = cat.load_catalog()
    other = next(m for m in sorted(catalog) if m != 1)
    shutil.copy(saved_page, saved_root / f"{other}.html")

    report = status_report(authored_root=authored_root, saved_root=saved_root)
    assert "imported (annotations.json exists): 1" in report
    assert f"map    1  {catalog[1].name}" in report
    assert "saved page but not yet imported: 1" in report
    assert f"map    {other}" in report
    assert "neither saved page nor annotations" in report
    assert "not in catalog (still importable): 1" in report
    assert "map    9" in report


def test_cli_end_to_end(saved_page, authored_root):
    """The real CLI: import, refuse-without-force, --force, --status."""
    base = [
        str(PYTHON),
        "-m",
        "riskdyn.workbench.import_page",
    ]
    run = lambda *extra: subprocess.run(  # noqa: E731
        base + list(extra), cwd=REPO_ROOT, capture_output=True, text=True
    )
    first = run("1", "--html", str(saved_page), "--authored-root", str(authored_root))
    assert first.returncode == 0, first.stderr
    assert (authored_root / "1" / "annotations.json").is_file()
    assert "42 territories" in first.stdout

    again = run("1", "--html", str(saved_page), "--authored-root", str(authored_root))
    assert again.returncode == 1
    assert "--force" in again.stderr

    forced = run(
        "1", "--html", str(saved_page), "--authored-root", str(authored_root),
        "--force",
    )
    assert forced.returncode == 0, forced.stderr

    status = run(
        "--status",
        "--authored-root", str(authored_root),
        "--saved-root", str(saved_page.parent),
    )
    assert status.returncode == 0, status.stderr
    assert "imported (annotations.json exists): 1" in status.stdout
