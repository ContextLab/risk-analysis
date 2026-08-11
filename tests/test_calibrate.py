"""Tests for calibration and inter-rater comparison, on real map 1 data."""
from __future__ import annotations

import pathlib

import pytest

from riskdyn.sources.d12.parse_topology import parse_topology
from riskdyn.workbench.calibrate import calibrate, compare_candidates, load_candidate
from riskdyn.workbench.graphs import undirected_edges

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "game_map1_territories.html"


def _doc_from_fixture() -> dict:
    """A schema-v2 candidate that reproduces map 1's ground truth exactly."""
    topology = parse_topology(FIXTURE.read_text(), 1)
    return {
        "schema_version": 2,
        "map_id": 1,
        "territories": [
            {
                "territory_id": t.territory_id,
                "name": t.name,
                "x": t.x,
                "y": t.y,
                "region_ids": [],
            }
            for t in topology.territories
        ],
        "edges": [
            {"a": a, "b": b, "kind": "unknown"} for a, b in undirected_edges(topology)
        ],
    }


def test_ground_truth_scores_perfectly_against_itself():
    """The instrument must read 1.0 on a known-perfect input, or it proves nothing."""
    report = calibrate(_doc_from_fixture(), FIXTURE)

    assert report["names"]["aligned"] == 42
    assert report["names"]["invented"] == []
    assert report["names"]["missed"] == []
    assert report["edges"]["true_positive"] == 83
    assert report["edges"]["precision"] == 1.0
    assert report["edges"]["recall"] == 1.0
    assert report["positions"]["max_px"] == 0.0


def test_dropping_edges_lowers_recall_but_not_precision():
    doc = _doc_from_fixture()
    doc["edges"] = doc["edges"][:-10]

    report = calibrate(doc, FIXTURE)

    assert report["edges"]["precision"] == 1.0
    assert report["edges"]["recall"] == pytest.approx(73 / 83)
    assert report["edges"]["false_negative"] == 10
    assert len(report["edges"]["missing"]) == 10


def test_comparison_of_a_document_with_itself_is_total_agreement():
    doc = _doc_from_fixture()

    report = compare_candidates(doc, doc)

    assert report["edges"]["jaccard"] == 1.0
    assert report["names"]["a_only"] == []
    assert report["names"]["b_only"] == []
    assert report["positions"]["max_px"] == 0.0


def test_comparison_reports_disagreement_symmetrically():
    """Two readers differing on one territory and one edge each.

    Agreement must be computed on the names they share, with the disputed
    territory's edges set aside as unscorable rather than blamed on either.
    """
    a = _doc_from_fixture()
    b = _doc_from_fixture()
    b["territories"][0]["name"] = "Atlantis"
    a["edges"] = a["edges"][:-1]

    report = compare_candidates(a, b)

    assert report["names"]["agreed"] == 41
    assert report["names"]["a_only"] == [a["territories"][0]["name"]]
    assert report["names"]["b_only"] == ["Atlantis"]
    assert report["edges"]["a_only"] == 0
    assert report["edges"]["unscorable"] > 0
    assert 0.9 < report["edges"]["jaccard"] < 1.0


def test_load_candidate_rejects_a_document_without_v2_keys(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"schema_version": 2, "map_id": 1}')

    with pytest.raises(ValueError, match="schema v2"):
        load_candidate(bad)
