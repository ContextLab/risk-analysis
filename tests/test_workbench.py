"""Workbench tests: overlap (criterion a), schema, graph, corrections, and
the real map-1 end-to-end build (uses the cached SAM masks -- no mocks)."""
from __future__ import annotations

import json
import pathlib
import re
import shutil

import numpy as np
import pytest

from riskdyn.maps.model import MapTopology, Territory
from riskdyn.workbench.bonuses import validate_bonus_entry, validate_bonuses
from riskdyn.workbench.checks import (
    check_b_all_present,
    check_c_no_nonplayable,
    check_d_bonuses,
)
from riskdyn.workbench.graphs import (
    measure_edge_gaps,
    propose_kind,
    undirected_edges,
)
from riskdyn.workbench.overlap import measure_overlap, resolve_overlaps
from riskdyn.workbench.provenance import apply_verification, make_provenance

REPO = pathlib.Path(__file__).resolve().parents[1]
MAP1_PROCESSED = REPO / "data" / "processed" / "maps" / "1"
MAP1_IMAGE = REPO / "data" / "raw" / "map_images" / "1.large.jpg"
MAP1_FIXTURE = REPO / "tests" / "fixtures" / "game_map1_territories.html"


# --------------------------------------------------------------------- a


def square(x0, y0, x1, y1):
    return ((float(x0), float(y0)), (float(x1), float(y0)),
            (float(x1), float(y1)), (float(x0), float(y1)))


def test_measure_overlap_detects_intersection():
    sets = {1: (square(0, 0, 10, 10),), 2: (square(8, 0, 18, 10),)}
    m = measure_overlap(sets)
    assert m["total_px2"] == pytest.approx(20.0)
    assert m["pairs"][0]["a"] == 1 and m["pairs"][0]["b"] == 2


def test_resolve_overlaps_reaches_zero_and_lower_id_wins():
    sets = {1: (square(0, 0, 10, 10),), 2: (square(8, 0, 18, 10),)}
    resolved = resolve_overlaps(sets)
    assert measure_overlap(resolved)["total_px2"] == 0.0
    from riskdyn.workbench.overlap import _to_geom

    assert _to_geom(resolved[1]).area == pytest.approx(100.0)  # id 1 kept
    assert _to_geom(resolved[2]).area < 100.0                  # id 2 ceded


def test_resolve_overlaps_refuses_to_vanish_a_territory():
    sets = {1: (square(0, 0, 20, 20),), 2: (square(5, 5, 8, 8),)}
    with pytest.raises(RuntimeError, match="vanished"):
        resolve_overlaps(sets)


def test_resolve_overlap_zero_at_artifact_precision():
    # jittered vertices that round into overlap at 0.1 px precision
    a = ((0.0, 0.0), (10.04, 0.0), (10.04, 10.0), (0.0, 10.0))
    b = ((10.01, 0.0), (20.0, 0.0), (20.0, 10.0), (10.01, 10.0))
    resolved = resolve_overlaps({1: (a,), 2: (b,)})
    for rings in resolved.values():
        for ring in rings:
            for x, y in ring:
                assert x == round(x, 1) and y == round(y, 1)
    assert measure_overlap(resolved)["total_px2"] == 0.0


# --------------------------------------------------------------------- d


def test_bonus_schema_holds_map100_realities():
    region_ids = {1}
    territory_ids = {10, 11, 12}
    edges = {(10, 11)}
    entries = [
        # plain per-region bonus
        {"kind": "region", "value": 5, "region_id": 1, "association": "explicit-label",
         "text_verbatim": "5", "bbox": [10, 10, 8, 12], "status": "resolved",
         "confidence": "high"},
        # numeral with no adjacent region name: association by colour, unresolved
        {"kind": "region", "value": 4, "region_id": None, "association": "by-colour",
         "text_verbatim": "4", "bbox": [100, 50, 8, 12], "status": "needs_review",
         "confidence": "low"},
        # scattered per-territory +1
        {"kind": "territory", "value": 1, "territory_id": 12,
         "text_verbatim": "+1", "bbox": [200, 90, 10, 10], "status": "resolved",
         "confidence": "medium"},
        # per-strait +1 attached to an edge
        {"kind": "edge", "value": 1, "edge": [11, 10], "text_verbatim": "+1",
         "bbox": [220, 95, 10, 10], "status": "resolved", "confidence": "medium"},
        # prose rule verbatim, never parsed
        {"kind": "prose", "value": None,
         "text_verbatim": "+5 for controlling all 5 Holy Cities",
         "bbox": [300, 400, 180, 14], "status": "needs_review",
         "confidence": "high"},
    ]
    for e in entries:
        assert validate_bonus_entry(e, region_ids, territory_ids, edges) == []
    # a whole doc with NO regions at all (map 77) and a grey territory in
    # no region is valid: regions empty, membership partial
    doc = {"regions": [], "bonuses": [entries[4]]}
    assert validate_bonuses(doc, territory_ids) == []


def test_bonus_schema_rejects_wrong_links():
    # unresolved region association may NOT claim to be resolved
    bad = {"kind": "region", "value": 4, "region_id": None,
           "association": "by-colour", "status": "resolved", "confidence": "low"}
    assert any("needs_review" in p for p in validate_bonus_entry(bad, set(), set()))
    # prose must stay unparsed: no structured application fields
    bad = {"kind": "prose", "value": 5, "region_id": 1,
           "text_verbatim": "+5 ...", "status": "needs_review", "confidence": "high"}
    problems = validate_bonus_entry(bad, {1}, set())
    assert any("never parsed" in p for p in problems)
    assert any("value null" in p for p in problems)
    # unknown territory
    bad = {"kind": "territory", "value": 1, "territory_id": 99,
           "status": "resolved", "confidence": "high"}
    assert validate_bonus_entry(bad, set(), {1}) != []


def test_check_d_needs_human_and_review_resolution():
    doc = {"regions": [], "bonuses": [
        {"kind": "prose", "value": None, "text_verbatim": "+2 somewhere",
         "bbox": None, "status": "needs_review", "confidence": "low"}]}
    r = check_d_bonuses(doc, set(), set(), None)
    assert r["status"] == "unverified"
    r = check_d_bonuses(doc, set(), set(),
                        {"bonuses_confirmed": {"verified": True, "by": "A Person"}})
    assert r["status"] == "unverified"  # entry still needs_review
    doc["bonuses"][0]["status"] = "resolved"
    doc["bonuses"][0]["kind"] = "region"
    doc["bonuses"][0]["region_id"] = None
    doc["bonuses"][0]["association"] = "by-colour"
    # region_id None resolved is a schema problem -> fail, never pass
    r = check_d_bonuses(doc, set(), set(),
                        {"bonuses_confirmed": {"verified": True, "by": "A Person"}})
    assert r["status"] == "fail"


# --------------------------------------------------------------------- b, c


def test_check_b_count_alone_is_not_proof():
    terrs = [{"territory_id": i, "name": None} for i in range(3)]
    r = check_b_all_present(terrs, 3, None)
    assert r["status"] == "unverified"          # right count, no name evidence
    r = check_b_all_present(terrs, 4, None)
    assert r["status"] == "fail"
    named = [{"territory_id": i, "name": n}
             for i, n in enumerate(["A", "B", "C"])]
    assert check_b_all_present(named, 3, ["A", "B", "C"])["status"] == "pass"
    # dropped real territory + admitted decoration keeps the count -- names catch it
    swapped = [{"territory_id": i, "name": n}
               for i, n in enumerate(["A", "B", "minimap-tile"])]
    r = check_b_all_present(swapped, 3, ["A", "B", "C"])
    assert r["status"] == "fail"
    assert r["missing_names"] == ["C"] and r["extra_names"] == ["minimap-tile"]


def test_check_c_requires_a_human():
    terrs = [{"territory_id": 1, "name": "A", "name_source": "d12-fixture-seed-claim"}]
    assert check_c_no_nonplayable(terrs, None)["status"] == "unverified"
    ok = {"overlay_confirmed": {"verified": True, "by": "A Person", "at": "2026-08-11"}}
    assert check_c_no_nonplayable(terrs, ok)["status"] == "pass"
    bad = [{"territory_id": 2, "name": "B", "name_source": None}]
    assert check_c_no_nonplayable(bad, ok)["status"] == "fail"


# --------------------------------------------------------------- provenance


def test_provenance_unverified_by_default_and_agent_refused():
    block = make_provenance("riskdyn.workbench.build", "test")
    assert block["human_verified"] is False
    assert apply_verification(block, None)["human_verified"] is False
    with pytest.raises(ValueError):
        apply_verification(block, {"verified": True, "by": "claude-agent"})
    stamped = apply_verification(
        block, {"verified": True, "by": "Jeremy Manning", "at": "2026-08-11"})
    assert stamped["human_verified"] is True
    assert stamped["verified_by"] == "Jeremy Manning"


# -------------------------------------------------------------------- graph


def _topo(edges_by_id: dict[int, tuple[int, ...]]) -> MapTopology:
    return MapTopology(1, tuple(
        Territory(tid, f"T{tid}", 0, 0, 0, adj)
        for tid, adj in edges_by_id.items()))


def test_undirected_edges_symmetry_guard():
    topo = _topo({1: (2,), 2: (1, 3), 3: (2,)})
    assert undirected_edges(topo) == [(1, 2), (2, 3)]
    with pytest.raises(ValueError, match="asymmetric"):
        undirected_edges(_topo({1: (2,), 2: ()}))


def test_edge_gap_wraps_cylindrically():
    rings = {1: (square(2, 10, 8, 20),), 2: (square(92, 10, 98, 20),)}
    gaps = measure_edge_gaps([(1, 2)], rings, (100, 50))
    g = gaps[(1, 2)]
    assert g["wraps"] is True
    assert g["gap_px"] < g["gap_direct_px"]
    assert g["gap_px"] == pytest.approx(4.0, abs=0.5)


def test_propose_kind_dead_band_is_unknown():
    assert propose_kind(1.0) == "shared-border"
    assert propose_kind(9.0) == "unknown"      # between the measured clusters
    assert propose_kind(30.0) == "route"
    assert propose_kind(None) == "unknown"


# -------------------------------------------------------- corrections unit


def test_apply_corrections_split_and_replace():
    from riskdyn.segment.geometry import extract_territories
    from riskdyn.workbench.build import apply_corrections

    label_map = np.zeros((40, 60), dtype=np.int32)
    label_map[5:15, 5:20] = 1     # component A of merged label
    label_map[25:35, 35:55] = 1   # component B of merged label
    label_map[5:15, 40:55] = 2    # a normal single-seed label
    shapes = extract_territories(label_map)
    by_label = {s.source_label: s for s in shapes}
    shapes = [by_label[1], by_label[2]]
    seed_groups = [(11, 32), (40,)]
    seed_xy = {11: (10.0, 10.0), 32: (45.0, 30.0)}

    # unresolved merge without a correction is reported, not hidden
    records, problems = apply_corrections(shapes, seed_groups, label_map, seed_xy, [])
    assert any("unresolved merge" in p for p in problems)

    corrections = [
        {"op": "split_components", "seed_ids": [11, 32]},
        {"op": "replace_outline", "territory_id": 40,
         "polygons": [[[40.0, 5.0], [55.0, 5.0], [55.0, 15.0], [40.0, 15.0]]],
         "source": "manual-svg-edit"},
    ]
    records, problems = apply_corrections(
        shapes, seed_groups, label_map, seed_xy, corrections)
    assert problems == []
    assert set(records) == {11, 32, 40}
    assert records[11]["outline_source"].endswith("split_components")
    assert records[40]["outline_source"] == "manual:manual-svg-edit"
    # split put each seed on its own component (11 upper-left, 32 lower-right)
    assert records[11]["centroid"][0] < records[32]["centroid"][0]

    # a split where one seed wins no component refuses loudly (both
    # components sit nearer seed 11 than the far-corner seed 32)
    with pytest.raises(ValueError, match="won no component"):
        apply_corrections(
            shapes, seed_groups, label_map, {11: (10.0, 10.0), 32: (0.0, 39.0)},
            [{"op": "split_components", "seed_ids": [11, 32]}])


# ------------------------------------------------------- map 1 end to end

requires_map1_data = pytest.mark.skipif(
    not (MAP1_PROCESSED / "sam_masks.npz").is_file()
    or not MAP1_IMAGE.is_file()
    or not MAP1_FIXTURE.is_file(),
    reason="map 1 artwork + cached SAM masks not present locally",
)


@pytest.fixture(scope="module")
def map1_build(tmp_path_factory):
    from riskdyn.workbench.build import build_map

    root = tmp_path_factory.mktemp("workbench")
    (root / "1").mkdir()
    shutil.copy(MAP1_PROCESSED / "sam_masks.npz", root / "1" / "sam_masks.npz")
    report = build_map(1, out_root=root)
    return root, report


@requires_map1_data
def test_map1_criteria_states(map1_build):
    _, report = map1_build
    crit = report["criteria"]
    assert crit["a_no_overlap"]["status"] == "pass"
    assert crit["a_no_overlap"]["overlap_px2"] == 0.0
    assert crit["b_all_territories"]["status"] == "pass"
    assert crit["b_all_territories"]["found_count"] == 42
    # c and d genuinely lack human confirmation: they must say so
    assert crit["c_no_nonplayable"]["status"] == "unverified"
    assert crit["d_bonuses_accurate"]["status"] == "unverified"
    assert report["overall"] == "unverified"
    assert report["provenance"]["human_verified"] is False


@requires_map1_data
def test_map1_artifacts_written_and_consistent(map1_build):
    root, _ = map1_build
    out = root / "1"
    for name in ("territories.json", "territories.svg", "graph.json",
                 "bonuses.json", "report.json", "overlay.png"):
        assert (out / name).is_file(), name

    tdoc = json.loads((out / "territories.json").read_text())
    assert len(tdoc["territories"]) == 42
    names = {t["name"] for t in tdoc["territories"]}
    from riskdyn.segment.ground_truth import load_label_points

    gt_names = {p.name for p in load_label_points(MAP1_FIXTURE)}
    assert names == gt_names
    assert all(t["region_id"] is not None for t in tdoc["territories"])

    # overlap measured on the polygons AS WRITTEN in the artifact
    rings = {t["territory_id"]: tuple(
        tuple((x, y) for x, y in ring) for ring in t["polygons"])
        for t in tdoc["territories"]}
    assert measure_overlap(rings)["total_px2"] == 0.0

    gdoc = json.loads((out / "graph.json").read_text())
    assert gdoc["n_edges"] == 83
    assert gdoc["wrap"]["horizontal"] is True
    wrap_edges = [(e["a"], e["b"]) for e in gdoc["edges"] if e["wraps"]]
    assert (60, 66) in wrap_edges  # Kamchatka-Alaska crosses the seam
    assert all(e["status"] in ("proposed", "confirmed") for e in gdoc["edges"])

    bdoc = json.loads((out / "bonuses.json").read_text())
    by_region = {r["name"]: r["region_id"] for r in bdoc["regions"]}
    values = {e["region_id"]: e["value"] for e in bdoc["bonuses"]
              if e["kind"] == "region"}
    assert values[by_region["North America"]] == 5
    assert values[by_region["Europe"]] == 5
    assert values[by_region["Asia"]] == 7
    assert values[by_region["South America"]] == 2
    assert values[by_region["Africa"]] == 3
    assert values[by_region["Australia"]] == 2

    svg = (out / "territories.svg").read_text()
    assert svg.count("<path id=\"territory-") == 42


@requires_map1_data
def test_map1_svg_roundtrip_imports_hand_edit(map1_build, tmp_path, monkeypatch):
    from riskdyn.workbench import build as wb

    root, _ = map1_build
    out = root / "1"
    # keep the real authored file intact: work on a copy
    authored = tmp_path / "authored"
    shutil.copytree(wb.AUTHORED_ROOT, authored)
    monkeypatch.setattr(wb, "AUTHORED_ROOT", authored)

    svg_path = out / "territories.svg"
    svg = svg_path.read_text()
    m = re.search(r'<path id="territory-66" d="M ([\d.]+),([\d.]+)', svg)
    assert m, "Alaska path missing"
    x, y = float(m.group(1)), float(m.group(2))
    svg = svg.replace(f"M {m.group(1)},{m.group(2)}", f"M {x + 2.0:.1f},{y:.1f}", 1)
    svg_path.write_text(svg)

    n = wb.sync_from_svg(1, out_root=root)
    assert n == 1
    ann = json.loads((authored / "1" / "annotations.json").read_text())
    ops = [c for c in ann["corrections"] if c["op"] == "replace_outline"]
    assert len(ops) == 1 and ops[0]["territory_id"] == 66
    assert ops[0]["source"] == "manual-svg-edit"
    assert ops[0]["polygons"][0][0] == [round(x + 2.0, 1), y]


def test_load_topology_fails_loudly_without_source():
    from riskdyn.workbench.build import load_topology

    with pytest.raises(FileNotFoundError, match="no adjacency source"):
        load_topology(2)
