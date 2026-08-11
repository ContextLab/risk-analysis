"""Score a vision-authored map graph against map 1's ground truth.

Usage:
    ./.venv/bin/python -m riskdyn.workbench.calibrate <candidate.json> [--json out.json]

Map 1 is the only map with independent ground truth: D12's own markup gives 42
canonical names and 83 undirected edges.  A *blind* authoring pass on it --
one that never saw the markup -- measures what the fan-out over the other 76
maps can be expected to produce.  Nothing else in the catalog can supply that
number, which is why this exists before the fan-out rather than after it.
"""
from __future__ import annotations

import argparse
import json
import pathlib

from riskdyn.segment.pipeline import MAP1_FIXTURE
from riskdyn.sources.d12.parse_topology import parse_topology
from riskdyn.workbench.graphs import undirected_edges
from riskdyn.workbench.score import align_names, score_edges, score_positions


def load_candidate(path: pathlib.Path) -> dict:
    doc = json.loads(path.read_text())
    if "territories" not in doc or "edges" not in doc:
        raise ValueError(f"{path}: expected schema v2 keys 'territories' and 'edges'")
    return doc


def calibrate(candidate: dict, fixture_path: pathlib.Path = MAP1_FIXTURE) -> dict:
    topology = parse_topology(fixture_path.read_text(), 1)
    reference_names = {t.territory_id: t.name for t in topology.territories}
    reference_points = {
        t.territory_id: (float(t.x), float(t.y)) for t in topology.territories
    }
    reference_edges = undirected_edges(topology)

    cand_names = {t["territory_id"]: t["name"] for t in candidate["territories"]}
    cand_points = {
        t["territory_id"]: (float(t["x"]), float(t["y"]))
        for t in candidate["territories"]
        if t.get("x") is not None and t.get("y") is not None
    }
    cand_edges = [(e["a"], e["b"]) for e in candidate["edges"]]

    alignment = align_names(cand_names, reference_names)
    edge_score = score_edges(cand_edges, reference_edges, alignment)

    by_kind: dict[str, int] = {}
    for e in candidate["edges"]:
        by_kind[e.get("kind", "unspecified")] = by_kind.get(e.get("kind", "unspecified"), 0) + 1

    return {
        "candidate_territories": len(cand_names),
        "reference_territories": len(reference_names),
        "candidate_edges": len(set((min(a, b), max(a, b)) for a, b in cand_edges)),
        "reference_edges": len(reference_edges),
        "names": {
            "aligned": len(alignment.matched),
            "exact": alignment.exact,
            "fuzzy": len(alignment.matched) - alignment.exact,
            "invented": [cand_names[c] for c in alignment.unmatched_candidate],
            "missed": [reference_names[r] for r in alignment.unmatched_reference],
        },
        "edges": {
            "true_positive": edge_score.true_positive,
            "false_positive": edge_score.false_positive,
            "false_negative": edge_score.false_negative,
            "unscorable": edge_score.unscorable,
            "precision": edge_score.precision,
            "recall": edge_score.recall,
            "f1": edge_score.f1,
            "kind_counts": by_kind,
            "missing": [
                f"{reference_names[a]} -- {reference_names[b]}"
                for a, b in edge_score.missing
            ],
            "spurious": [
                f"{reference_names[a]} -- {reference_names[b]}"
                for a, b in edge_score.spurious
            ],
        },
        "positions": score_positions(cand_points, reference_points, alignment),
    }


def _format(report: dict) -> str:
    n, e = report["names"], report["edges"]
    lines = [
        f"territories: {report['candidate_territories']} authored "
        f"vs {report['reference_territories']} known",
        f"names:       {n['aligned']} aligned ({n['exact']} exact, {n['fuzzy']} fuzzy), "
        f"{len(n['invented'])} invented, {len(n['missed'])} missed",
        f"edges:       {report['candidate_edges']} authored vs {report['reference_edges']} known",
        f"             TP {e['true_positive']}  FP {e['false_positive']}  "
        f"FN {e['false_negative']}  unscorable {e['unscorable']}",
    ]
    if e["precision"] is not None:
        lines.append(
            f"             precision {e['precision']:.3f}  recall {e['recall']:.3f}"
            + (f"  F1 {e['f1']:.3f}" if e["f1"] else "")
        )
    lines.append(f"             kinds {e['kind_counts']}")
    lines.append(
        f"nodes:       median {report['positions']['median_px']} px from anchor, "
        f"max {report['positions']['max_px']} px"
    )
    if n["missed"]:
        lines.append("missed names:   " + ", ".join(n["missed"]))
    if n["invented"]:
        lines.append("invented names: " + ", ".join(n["invented"]))
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=pathlib.Path)
    parser.add_argument("--json", type=pathlib.Path, default=None)
    args = parser.parse_args(argv)

    report = calibrate(load_candidate(args.candidate))
    print(_format(report))
    if args.json:
        args.json.write_text(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
