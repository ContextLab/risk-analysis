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
    assert all(n["region_id"] in {1, 2, 3, 4, 5, 6} for n in ndoc["nodes"])
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


# ------------------------------------------------- validation fails loudly


def _map1_doc() -> dict:
    return json.loads(MAP1_ANNOTATIONS.read_text())


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
        doc["territories"][0]["region_id"] = 42

    with pytest.raises(ValueError, match="not present in regions"):
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
    doc["territories"][5]["region_id"] = 9
    with pytest.raises(ValueError) as exc:
        validate_annotations(doc, 1021, 689)
    msg = str(exc.value)
    assert "outside image bounds" in msg
    assert "self-loop" in msg
    assert "not present in regions" in msg
