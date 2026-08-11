"""Graphs-only build tests: real map-1 data end to end (no mocks), plus the
loud-failure validation contract on deliberately bad annotations."""
from __future__ import annotations

import copy
import json
import pathlib

import pytest

from riskdyn.workbench.graph_build import (
    build_graph_map,
    load_annotations_v2,
    validate_annotations,
)

REPO = pathlib.Path(__file__).resolve().parents[1]
MAP1_ANNOTATIONS = REPO / "data" / "authored" / "maps" / "1" / "annotations.json"
MAP1_IMAGE = REPO / "data" / "raw" / "map_images" / "1.large.jpg"

requires_map1_data = pytest.mark.skipif(
    not MAP1_IMAGE.is_file(), reason="map 1 artwork not present locally"
)

ARTIFACTS = ("nodes.json", "graph.json", "bonuses.json", "report.json", "overlay.png")


# ------------------------------------------------------- map 1 end to end


@pytest.fixture(scope="module")
def map1_graph_build(tmp_path_factory):
    root = tmp_path_factory.mktemp("graph_build")
    report = build_graph_map(1, out_root=root)
    return root / "1", report


@requires_map1_data
def test_map1_emits_all_five_artifacts(map1_graph_build):
    out, _ = map1_graph_build
    for name in ARTIFACTS:
        assert (out / name).is_file(), name


@requires_map1_data
def test_map1_nodes_and_graph_contents(map1_graph_build):
    out, _ = map1_graph_build
    ndoc = json.loads((out / "nodes.json").read_text())
    assert len(ndoc["nodes"]) == 42
    assert all(n["name"] for n in ndoc["nodes"])
    # membership is a list (many-to-many); on map 1 every territory sits in
    # exactly one of the six continents
    assert all(isinstance(n["region_ids"], list) for n in ndoc["nodes"])
    assert all(len(n["region_ids"]) == 1 for n in ndoc["nodes"])
    assert all(n["region_ids"][0] in {1, 2, 3, 4, 5, 6} for n in ndoc["nodes"])
    assert all(n["source"] == "d12-markup" for n in ndoc["nodes"])

    gdoc = json.loads((out / "graph.json").read_text())
    assert gdoc["n_edges"] == 83
    assert len(gdoc["edges"]) == 83
    assert gdoc["connected"] is True
    assert gdoc["n_components"] == 1
    assert gdoc["degree"]["min"] == 2
    assert gdoc["degree"]["max"] == 6
    # every edge exists per D12's own markup; kinds remain a proposal
    assert all(e["status"] == "confirmed" for e in gdoc["edges"])
    assert all(e["kind_status"] == "proposed" for e in gdoc["edges"])
    assert all(e["kind"] in ("shared-border", "route") for e in gdoc["edges"])
    wrap_edges = [(e["a"], e["b"]) for e in gdoc["edges"] if e["wraps"]]
    assert wrap_edges == [(60, 66)]  # Kamchatka-Alaska crosses the seam
    assert gdoc["wrap"]["horizontal"] is True


@requires_map1_data
def test_map1_report_criteria(map1_graph_build):
    _, report = map1_graph_build
    crit = report["criteria"]
    assert crit["b_all_territories"]["status"] == "pass"
    assert crit["b_all_territories"]["found_count"] == 42
    assert crit["e_graph_confirmed"]["status"] == "pass"
    assert crit["e_graph_confirmed"]["n_kind_unconfirmed"] == 83
    assert crit["f_regions"]["status"] == "pass"
    # no human has signed off on the bonuses: d must say so
    assert crit["d_bonuses_accurate"]["status"] == "unverified"
    assert report["overall"] == "unverified"
    assert report["provenance"]["human_verified"] is False


@requires_map1_data
def test_map1_bonuses_document(map1_graph_build):
    out, _ = map1_graph_build
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
    assert bdoc["special_rules"] == []
    # region membership assembled from the authored territories
    assert sum(len(r["territory_ids"]) for r in bdoc["regions"]) == 42


@requires_map1_data
def test_map1_overlay_is_readable_png(map1_graph_build):
    out, _ = map1_graph_build
    path = out / "overlay.png"
    assert path.stat().st_size > 0
    from PIL import Image

    with Image.open(path) as im:
        assert im.format == "PNG"
        assert im.width > 100 and im.height > 100


# --------------------------------------- region membership is many-to-many


def _map1_doc() -> dict:
    return json.loads(MAP1_ANNOTATIONS.read_text())


def _build_mutated(tmp_path, mutate):
    """Build a mutated copy of the real map-1 annotations; returns
    (report, out_dir)."""
    doc = copy.deepcopy(_map1_doc())
    mutate(doc)
    authored = tmp_path / "authored"
    (authored / "1").mkdir(parents=True)
    (authored / "1" / "annotations.json").write_text(json.dumps(doc))
    report = build_graph_map(1, out_root=tmp_path / "out", authored_root=authored)
    return report, tmp_path / "out" / "1"


@requires_map1_data
def test_territory_in_two_regions_counted_once_per_region(tmp_path):
    """A territory with two region ids is accepted and appears in BOTH
    regions' member lists (the map-7 overlap shape: a city bonus overlapping
    a colour region)."""
    def mutate(doc):
        t = doc["territories"][0]
        assert t["region_ids"] == [1]
        t["region_ids"] = [1, 2]

    report, out = _build_mutated(tmp_path, mutate)
    crit_f = report["criteria"]["f_regions"]
    assert crit_f["status"] == "pass"  # all 6 regions still used
    assert crit_f["n_territories_multiple_regions"] == 1
    assert crit_f["n_territories_no_region"] == 0

    doc = json.loads((MAP1_ANNOTATIONS).read_text())
    tid = doc["territories"][0]["territory_id"]
    bdoc = json.loads((out / "bonuses.json").read_text())
    members = {r["region_id"]: r["territory_ids"] for r in bdoc["regions"]}
    assert tid in members[1] and tid in members[2]  # once per region
    assert members[1].count(tid) == 1 and members[2].count(tid) == 1

    ndoc = json.loads((out / "nodes.json").read_text())
    node = next(n for n in ndoc["nodes"] if n["territory_id"] == tid)
    assert node["region_ids"] == [1, 2]


@requires_map1_data
def test_f_passes_with_empty_membership_when_all_regions_used(tmp_path):
    """region_ids: [] is legal: as long as the territories collectively use
    every catalog region, f passes and the no-region count is reported."""
    def mutate(doc):
        # region 1 (North America) keeps 8 other members, so all 6 regions
        # remain in use
        doc["territories"][0]["region_ids"] = []

    report, _ = _build_mutated(tmp_path, mutate)
    crit_f = report["criteria"]["f_regions"]
    assert crit_f["status"] == "pass"
    assert crit_f["distinct_regions_used"] == 6
    assert crit_f["n_territories_no_region"] == 1
    assert crit_f["territories_no_region"] == [
        _map1_doc()["territories"][0]["territory_id"]
    ]


def test_f_fails_when_only_six_of_twelve_regions_used():
    """The map 7 defect: 6 colour regions + 6 overlapping city bonuses = 12
    catalog regions.  Authoring only the 6 colour groups must FAIL f, not
    score 6 of 12 as complete."""
    from riskdyn.workbench.graph_build import _crit_f

    regions = [{"region_id": r} for r in range(1, 7)]
    territories = [
        {"territory_id": i, "region_ids": [1 + i % 6]} for i in range(24)
    ]
    crit = _crit_f(territories, regions, expected_regions=12)
    assert crit["status"] == "fail"
    assert crit["expected_regions"] == 12
    assert crit["distinct_regions_used"] == 6


def test_f_fails_when_a_defined_region_is_unused():
    from riskdyn.workbench.graph_build import _crit_f

    regions = [{"region_id": 1}, {"region_id": 2}]
    territories = [
        {"territory_id": 1, "region_ids": [1]},
        {"territory_id": 2, "region_ids": [1, 3]},
    ]
    crit = _crit_f(territories, regions, expected_regions=2)
    assert crit["status"] == "fail"
    assert crit["defined_regions_unused"] == [2]


def test_f_fails_when_no_regions_authored():
    """Every imported map today: regions [] and every region_ids [].  The
    regions are genuinely absent, so f is fail -- not unverified."""
    from riskdyn.workbench.graph_build import _crit_f

    territories = [{"territory_id": i, "region_ids": []} for i in range(5)]
    crit = _crit_f(territories, [], expected_regions=12)
    assert crit["status"] == "fail"
    assert crit["distinct_regions_used"] == 0
    assert crit["n_territories_no_region"] == 5


def test_import_page_produces_empty_region_ids_lists(tmp_path):
    """Importing a saved page yields region_ids: [] for every territory --
    never the retired scalar, never [0]."""
    from riskdyn.workbench.import_page import import_page

    fixture = pathlib.Path(__file__).parent / "fixtures" / "game_map1_territories.html"
    authored = tmp_path / "authored"
    result = import_page(1, html_path=fixture, authored_root=authored)
    doc = json.loads(result.path.read_text())
    assert len(doc["territories"]) == 42
    for t in doc["territories"]:
        assert "region_id" not in t
        assert t["region_ids"] == []
    # and the written file passes the builder's own validation
    validate_annotations(doc, 1021, 689)


# ------------------------------------------------- validation fails loudly


def _build_bad(tmp_path, mutate) -> None:
    """Write a mutated copy of the real map-1 annotations and build it."""
    doc = copy.deepcopy(_map1_doc())
    mutate(doc)
    authored = tmp_path / "authored"
    (authored / "1").mkdir(parents=True)
    (authored / "1" / "annotations.json").write_text(json.dumps(doc))
    build_graph_map(1, out_root=tmp_path / "out", authored_root=authored)


def test_rejects_duplicate_territory_id(tmp_path):
    def mutate(doc):
        doc["territories"][1]["territory_id"] = doc["territories"][0]["territory_id"]

    with pytest.raises(ValueError, match="duplicate territory_id"):
        _build_bad(tmp_path, mutate)


def test_rejects_duplicate_normalized_name(tmp_path):
    def mutate(doc):
        # normalize_name folds "W. Europe" and "Western Europe" together
        by_name = {t["name"]: t for t in doc["territories"]}
        by_name["Eastern Europe" if "Eastern Europe" in by_name else "Ukraine"][
            "name"
        ] = "W. Europe"
        assert "Western Europe" in by_name

    with pytest.raises(ValueError, match="duplicate normalized name"):
        _build_bad(tmp_path, mutate)


def test_rejects_edge_to_unknown_territory(tmp_path):
    def mutate(doc):
        doc["edges"][0]["b"] = 99999

    with pytest.raises(ValueError, match="unknown territory_id"):
        _build_bad(tmp_path, mutate)


def test_rejects_self_loop(tmp_path):
    def mutate(doc):
        doc["edges"][0]["b"] = doc["edges"][0]["a"]

    with pytest.raises(ValueError, match="self-loop"):
        _build_bad(tmp_path, mutate)


def test_rejects_duplicate_undirected_edge(tmp_path):
    def mutate(doc):
        first = doc["edges"][0]
        doc["edges"].append({**first, "a": first["b"], "b": first["a"]})

    with pytest.raises(ValueError, match="duplicate undirected edge"):
        _build_bad(tmp_path, mutate)


def test_rejects_unknown_region_id(tmp_path):
    def mutate(doc):
        doc["territories"][0]["region_ids"] = [42]

    with pytest.raises(ValueError, match="not present in regions"):
        _build_bad(tmp_path, mutate)


def test_rejects_retired_scalar_region_id_key(tmp_path):
    """No compatibility shim: the old scalar key must raise, never be
    silently coerced into a one-element list."""
    def mutate(doc):
        t = doc["territories"][0]
        t["region_id"] = t.pop("region_ids")[0]

    with pytest.raises(ValueError, match="retired scalar 'region_id'"):
        _build_bad(tmp_path, mutate)


def test_rejects_missing_region_ids_list(tmp_path):
    def mutate(doc):
        del doc["territories"][0]["region_ids"]

    with pytest.raises(ValueError, match="no 'region_ids' list"):
        _build_bad(tmp_path, mutate)


def test_rejects_duplicate_region_in_one_territory(tmp_path):
    def mutate(doc):
        doc["territories"][0]["region_ids"] = [1, 1]

    with pytest.raises(ValueError, match="more than once"):
        _build_bad(tmp_path, mutate)


def test_rejects_coordinates_outside_image(tmp_path):
    def mutate(doc):
        doc["territories"][0]["x"] = 5000  # map 1 is 1021x689

    with pytest.raises(ValueError, match="outside image bounds"):
        _build_bad(tmp_path, mutate)


def test_rejects_confirmation_for_missing_edge(tmp_path):
    def mutate(doc):
        doc["edge_confirmations"] = [
            {"a": 5, "b": 40, "kind": "route", "by": "Jeremy Manning"}
        ]
        assert not any(
            {e["a"], e["b"]} == {5, 40} for e in doc["edges"]
        ), "test edge must not exist"

    with pytest.raises(ValueError, match="non-existent edge"):
        _build_bad(tmp_path, mutate)


def test_rejects_v1_schema_and_wrong_map_id(tmp_path):
    doc = copy.deepcopy(_map1_doc())
    doc["schema_version"] = 1
    p = tmp_path / "annotations.json"
    p.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="schema_version"):
        load_annotations_v2(p, 1)
    doc["schema_version"] = 2
    p.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="map_id"):
        load_annotations_v2(p, 7)
    with pytest.raises(FileNotFoundError):
        load_annotations_v2(tmp_path / "nope" / "annotations.json", 1)


def test_validate_reports_every_problem_at_once():
    doc = copy.deepcopy(_map1_doc())
    doc["territories"][0]["x"] = -3
    doc["edges"][0]["b"] = doc["edges"][0]["a"]
    doc["territories"][5]["region_ids"] = [9]
    with pytest.raises(ValueError) as exc:
        validate_annotations(doc, 1021, 689)
    msg = str(exc.value)
    assert "outside image bounds" in msg
    assert "self-loop" in msg
    assert "not present in regions" in msg
