"""Tests for the authored-graph scorer, against the real map 1 fixture."""
from __future__ import annotations

import pathlib

import pytest

from riskdyn.sources.d12.parse_topology import parse_topology
from riskdyn.workbench.graphs import undirected_edges
from riskdyn.workbench.score import (
    align_names,
    normalize_name,
    score_edges,
    score_positions,
)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "game_map1_territories.html"


@pytest.fixture(scope="module")
def topology():
    return parse_topology(FIXTURE.read_text(), 1)


@pytest.mark.parametrize(
    "printed,canonical",
    [
        ("NW Territory", "Northwest Territory"),
        ("W. Europe", "Western Europe"),
        ("N. Europe", "Northern Europe"),
        ("Western US", "Western United States"),
        ("E. Australia", "Eastern Australia"),
        ("S. Africa", "South Africa"),
    ],
)
def test_abbreviations_normalize_to_canonical(printed, canonical):
    assert normalize_name(printed) == normalize_name(canonical)


def test_normalize_is_insensitive_to_punctuation_and_case():
    assert normalize_name("Papua New  Guinea!") == "papua new guinea"


def test_align_names_matches_all_42_from_artwork_abbreviations(topology):
    """The printed forms on the artwork must align to the canonical markup names.

    This is the property the whole calibration rests on: if abbreviation
    handling fails, correct graphs get scored as broken.
    """
    canonical = {t.territory_id: t.name for t in topology.territories}
    abbreviate = {
        "Northwest Territory": "NW Territory",
        "Western United States": "Western US",
        "Eastern United States": "Eastern US",
        "Northern Europe": "N. Europe",
        "Southern Europe": "S. Europe",
        "Western Europe": "W. Europe",
        "Western Australia": "W. Australia",
        "Eastern Australia": "E. Australia",
        "North Africa": "N. Africa",
        "South Africa": "S. Africa",
        "Middle East": "Mid East",
    }
    # Candidate ids are deliberately different from reference ids: alignment
    # must work by name, not by id coincidence.
    candidate = {
        1000 + i: abbreviate.get(name, name)
        for i, name in enumerate(canonical.values())
    }

    alignment = align_names(candidate, canonical)

    assert len(alignment.matched) == 42
    assert alignment.unmatched_candidate == ()
    assert alignment.unmatched_reference == ()


def test_align_names_reports_a_hallucinated_territory_as_unmatched(topology):
    canonical = {t.territory_id: t.name for t in topology.territories}
    candidate = {1: "Alaska", 2: "Atlantis"}

    alignment = align_names(candidate, canonical)

    assert alignment.matched[1] == next(
        t.territory_id for t in topology.territories if t.name == "Alaska"
    )
    assert alignment.unmatched_candidate == (2,)


def test_identical_graph_scores_perfectly(topology):
    canonical = {t.territory_id: t.name for t in topology.territories}
    edges = undirected_edges(topology)
    alignment = align_names(canonical, canonical)

    score = score_edges(edges, edges, alignment)

    assert score.true_positive == 83
    assert score.false_positive == 0
    assert score.false_negative == 0
    assert score.precision == 1.0
    assert score.recall == 1.0


def test_missing_and_spurious_edges_are_counted_and_named(topology):
    canonical = {t.territory_id: t.name for t in topology.territories}
    reference = undirected_edges(topology)
    alignment = align_names(canonical, canonical)
    dropped = reference[0]
    ids = sorted(canonical)
    invented = next(
        (a, b)
        for i, a in enumerate(ids)
        for b in ids[i + 1 :]
        if (a, b) not in set(reference)
    )
    candidate = [e for e in reference if e != dropped] + [invented]

    score = score_edges(candidate, reference, alignment)

    assert score.false_negative == 1
    assert score.missing == (dropped,)
    assert score.false_positive == 1
    assert score.spurious == (tuple(sorted(invented)),)
    assert score.true_positive == 82


def test_edges_touching_an_unaligned_territory_are_unscorable_not_errors(topology):
    """A name we could not align must not be charged as a graph error.

    The candidate edge into the unaligned territory counts as unscorable, and
    the reference edges into its counterpart are excluded from recall.
    """
    canonical = {t.territory_id: t.name for t in topology.territories}
    alaska = next(t for t in topology.territories if t.name == "Alaska")
    candidate_names = {
        tid: ("Atlantis" if tid == alaska.territory_id else name)
        for tid, name in canonical.items()
    }
    reference = undirected_edges(topology)
    alignment = align_names(candidate_names, canonical)
    assert alignment.unmatched_candidate == (alaska.territory_id,)

    score = score_edges(reference, reference, alignment)

    alaska_edges = sum(1 for e in reference if alaska.territory_id in e)
    assert score.unscorable == alaska_edges
    assert score.false_positive == 0
    assert score.false_negative == 0
    assert score.true_positive == 83 - alaska_edges


def test_score_positions_measures_offset_from_anchors(topology):
    canonical = {t.territory_id: t.name for t in topology.territories}
    anchors = {t.territory_id: (float(t.x), float(t.y)) for t in topology.territories}
    shifted = {tid: (x + 3.0, y + 4.0) for tid, (x, y) in anchors.items()}
    alignment = align_names(canonical, canonical)

    result = score_positions(shifted, anchors, alignment)

    assert result["n"] == 42
    assert result["median_px"] == 5.0
    assert result["max_px"] == 5.0
