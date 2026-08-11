"""Build the per-map artifact set from segmentation + authored annotations.

Data flow (all regenerable, so correction is a round-trip, not a fork):

    artwork + cached SAM masks + seed source      (stage 1, riskdyn.segment)
        -> shapes with seed claims
    data/authored/maps/<id>/annotations.json      (ALL manual work lives here)
        -> corrections (splits, outline replacements), regions, bonuses,
           edge confirmations, human verification sign-offs
    ==> data/processed/maps/<id>/{territories.json, territories.svg,
        graph.json, bonuses.json, report.json, overlay.png}

To correct a map: edit annotations.json (or hand-edit territories.svg and
run ``--from-svg``, which imports the edited outlines back into
annotations.json as replace_outline corrections), then re-run

    ./.venv/bin/python -m riskdyn.workbench.build <map_id>

which regenerates every derived artifact and re-runs the (a)-(d) checks.
Nothing under data/processed is ever the only copy of manual work.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re

import numpy as np

from riskdyn.maps.model import MapTopology
from riskdyn.segment import catalog as cat
from riskdyn.segment.geometry import extract_territories
from riskdyn.segment.ground_truth import load_label_points
from riskdyn.segment.pipeline import MAP1_FIXTURE, run_map
from riskdyn.workbench.checks import (
    check_a_no_overlap,
    check_b_all_present,
    check_c_no_nonplayable,
    check_d_bonuses,
)
from riskdyn.workbench.graphs import build_graph
from riskdyn.workbench.overlap import Ring, measure_overlap, resolve_overlaps
from riskdyn.workbench.provenance import make_provenance

AUTHORED_ROOT = cat.REPO_ROOT / "data" / "authored" / "maps"
PROCESSED_ROOT = cat.REPO_ROOT / "data" / "processed" / "maps"


# --------------------------------------------------------------------------
# topology sources (adjacency + names ground truth), pluggable like seeds
# --------------------------------------------------------------------------

def _map1_topology(map_id: int):
    if map_id != 1 or not MAP1_FIXTURE.is_file():
        return None
    from riskdyn.sources.d12.parse_topology import parse_topology

    return parse_topology(MAP1_FIXTURE.read_text(), map_id), "d12-fixture-adjacencies"


TOPOLOGY_SOURCES = (_map1_topology,)


def load_topology(map_id: int) -> tuple[MapTopology, str]:
    for source in TOPOLOGY_SOURCES:
        got = source(map_id)
        if got is not None:
            return got
    raise FileNotFoundError(
        f"no adjacency source for map {map_id}: graph.json cannot be built. "
        "Adjacency is manual/vision-assisted per-map work (plan v3); add a "
        "topology source or authored edge list before building this map."
    )


# --------------------------------------------------------------------------
# annotations
# --------------------------------------------------------------------------

def annotations_path(map_id: int) -> pathlib.Path:
    return AUTHORED_ROOT / str(map_id) / "annotations.json"


def load_annotations(map_id: int) -> dict:
    path = annotations_path(map_id)
    if not path.is_file():
        return {"schema_version": 1, "map_id": map_id}
    doc = json.loads(path.read_text())
    if doc.get("map_id") != map_id:
        raise ValueError(f"{path} declares map_id {doc.get('map_id')}, expected {map_id}")
    return doc


# --------------------------------------------------------------------------
# corrections
# --------------------------------------------------------------------------

def _split_label_by_seeds(
    label_map: np.ndarray, label: int, seeds: list[tuple[int, float, float]]
) -> dict[int, list]:
    """Split one label's connected components among seed points.

    Each component goes to the seed with the smallest point-to-component
    pixel distance.  Every seed must win at least one component -- if not,
    the split is wrong and we refuse rather than emit a phantom territory.
    Returns {seed_id: shapes} via extract_territories on a sub-label-map.
    """
    import cv2

    mask = (label_map == label).astype(np.uint8)
    if not mask.any():
        raise ValueError(f"split_components: label {label} has no pixels")
    n_comp, comp = cv2.connectedComponents(mask, connectivity=4)
    sub = np.zeros_like(label_map)
    won: dict[int, int] = {}
    for c in range(1, n_comp):
        ys, xs = np.nonzero(comp == c)
        dists = [
            float(np.min((xs - sx) ** 2 + (ys - sy) ** 2))
            for _, sx, sy in seeds
        ]
        k = int(np.argmin(dists))
        sub[comp == c] = k + 1
        won[k] = won.get(k, 0) + 1
    missing = [seeds[k][0] for k in range(len(seeds)) if k not in won]
    if missing:
        raise ValueError(
            f"split_components: seeds {missing} won no component; the merged "
            "mask does not separate into per-seed islands -- fix by hand "
            "(replace_outline) instead"
        )
    shapes = extract_territories(sub)
    return {seeds[s.source_label - 1][0]: s for s in shapes}


def apply_corrections(
    shapes: list,
    seed_groups: list[tuple[int, ...]],
    label_map: np.ndarray,
    seed_xy: dict[int, tuple[float, float]],
    corrections: list[dict],
) -> tuple[dict[int, dict], list[str]]:
    """Shapes + corrections -> {seed/territory id: geometry record}.

    Returns (records, problems).  A label claimed by multiple seeds MUST be
    covered by a split_components correction (or replace_outline for every
    involved territory); otherwise it is reported as an unresolved merge.
    """
    records: dict[int, dict] = {}
    problems: list[str] = []
    split_ops = {
        tuple(sorted(op["seed_ids"])): op
        for op in corrections
        if op.get("op") == "split_components"
    }
    replace_ops = {
        op["territory_id"]: op
        for op in corrections
        if op.get("op") == "replace_outline"
    }

    for shape in shapes:
        group = seed_groups[shape.source_label - 1]
        if len(group) == 1:
            sid = group[0]
            records[sid] = {
                "polygons": shape.polygons,
                "centroid": shape.centroid,
                "area_px": shape.area_px,
                "flags": list(shape.flags),
                "outline_source": "sam+seed-selection",
            }
            continue
        key = tuple(sorted(group))
        if key in split_ops:
            seeds = [(sid, *seed_xy[sid]) for sid in key]
            for sid, s in _split_label_by_seeds(
                label_map, shape.source_label, seeds
            ).items():
                records[sid] = {
                    "polygons": s.polygons,
                    "centroid": s.centroid,
                    "area_px": s.area_px,
                    "flags": list(s.flags),
                    "outline_source": "sam+seed-selection+split_components",
                }
        elif all(sid in replace_ops for sid in key):
            pass  # every member replaced below
        else:
            problems.append(
                f"unresolved merge: seeds {list(key)} share one mask; add a "
                "split_components or replace_outline correction"
            )

    for tid, op in replace_ops.items():
        polygons = tuple(
            tuple((float(x), float(y)) for x, y in ring) for ring in op["polygons"]
        )
        xs = [x for ring in polygons for x, _ in ring]
        ys = [y for ring in polygons for _, y in ring]
        records[tid] = {
            "polygons": polygons,
            "centroid": (float(np.mean(xs)), float(np.mean(ys))),
            "area_px": 0,  # recomputed after overlap resolution
            "flags": [],
            "outline_source": f"manual:{op.get('source', 'replace_outline')}",
        }
    return records, problems


# --------------------------------------------------------------------------
# artifact assembly
# --------------------------------------------------------------------------

def _region_tables(
    annotations: dict, name_to_id: dict[str, int]
) -> tuple[list[dict], dict[int, int]]:
    """Authored regions -> (region defs with ids, territory_id -> region_id)."""
    regions = []
    membership: dict[int, int] = {}
    for r in annotations.get("regions", []):
        member_ids = []
        for name in r.get("territory_names", []):
            if name not in name_to_id:
                raise ValueError(
                    f"region {r.get('name')!r} lists unknown territory {name!r}"
                )
            tid = name_to_id[name]
            if tid in membership:
                raise ValueError(f"territory {name!r} assigned to two regions")
            membership[tid] = r["region_id"]
            member_ids.append(tid)
        regions.append(
            {
                "region_id": r["region_id"],
                "name": r.get("name"),
                "territory_ids": sorted(member_ids),
            }
        )
    return regions, membership


def _bonuses_doc(annotations: dict, regions: list[dict], map_id: int) -> dict:
    entries = []
    for r in annotations.get("regions", []):
        entries.append(
            {
                "kind": "region",
                "value": r.get("bonus"),
                "region_id": r["region_id"],
                "association": r.get("association", "explicit-label"),
                "text_verbatim": r.get("bonus_text_verbatim"),
                "bbox": r.get("bonus_bbox"),
                "status": r.get("bonus_status", "resolved"),
                "confidence": r.get("confidence", "low"),
                "provenance": make_provenance(
                    r.get("source", "unknown"),
                    "transcribed from artwork legend",
                    note=r.get("note"),
                ),
            }
        )
    entries.extend(annotations.get("extra_bonuses", []))
    return {
        "schema_version": 1,
        "map_id": map_id,
        "regions": regions,
        "bonuses": entries,
        "provenance": make_provenance(
            "riskdyn.workbench.build",
            "assembled from authored annotations.json; bonus values have NO "
            "automated ground truth -- see per-entry provenance/confidence",
            inputs=[str(annotations_path(map_id).relative_to(cat.REPO_ROOT))],
        ),
    }


def _write_svg(territories: list[dict], size: tuple[int, int], path: pathlib.Path) -> None:
    """territories.svg, one <path> per territory, id = territory-<d12 id>.

    Hand-editable: change a path's ``d`` and run ``--from-svg`` to import
    the edit back into annotations.json (see sync_from_svg).
    """
    w, h = size

    def ring_d(ring: Ring) -> str:
        return "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in ring) + " Z"

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}">',
    ]
    for t in territories:
        d = " ".join(ring_d(r) for r in t["polygons"])
        cx, cy = t["centroid"]
        lines.append(
            f'  <path id="territory-{t["territory_id"]}" d="{d}" '
            f'data-name="{t["name"]}" data-centroid="{cx:.1f},{cy:.1f}" '
            f'data-area-px="{t["area_px"]}" data-n-polygons="{len(t["polygons"])}" '
            f'fill="none" stroke="black" stroke-width="1"/>'
        )
    lines.append("</svg>")
    path.write_text("\n".join(lines) + "\n")


def _write_overlay(
    image: np.ndarray, territories: list[dict], header: str, path: pathlib.Path
) -> None:
    """Named overlay for human verification (matplotlib, PNG)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    h, w = image.shape[:2]
    fig, ax = plt.subplots(figsize=(w / 72, h / 72), dpi=144)
    ax.imshow(image)
    cmap = plt.get_cmap("tab20")
    for i, t in enumerate(sorted(territories, key=lambda t: t["territory_id"])):
        color = cmap(i % 20)
        for ring in t["polygons"]:
            xs = [p[0] for p in ring] + [ring[0][0]]
            ys = [p[1] for p in ring] + [ring[0][1]]
            ax.plot(xs, ys, color=color, linewidth=1.2)
        cx, cy = t["centroid"]
        ax.text(
            cx, cy, t["name"], color="white", fontsize=5, ha="center",
            va="center", bbox=dict(facecolor="black", alpha=0.55, pad=0.6, lw=0),
        )
    ax.set_title(header, fontsize=8)
    ax.set_xlim(0, w)
    ax.set_ylim(h, 0)
    ax.axis("off")
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


# --------------------------------------------------------------------------
# the build
# --------------------------------------------------------------------------

def build_map(map_id: int, out_root: pathlib.Path | None = None) -> dict:
    """Full workbench build for one map; returns the report dict."""
    out_dir = (out_root or PROCESSED_ROOT) / str(map_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = cat.load_catalog()[map_id]
    annotations = load_annotations(map_id)
    topology, edge_source = load_topology(map_id)
    gt_names = {t.territory_id: t.name for t in topology.territories}

    # stage 1 (SAM cached in the map's processed dir; no network)
    result = run_map(map_id, out_root or PROCESSED_ROOT, write_artifacts=False)
    if not result.seeded or result.seed_groups is None:
        raise RuntimeError(
            f"map {map_id} has no seed source; the workbench build requires "
            "seed-claimed shapes so names have provenance"
        )
    seed_xy = {p.territory_id: (float(p.x), float(p.y)) for p in load_label_points(MAP1_FIXTURE)} \
        if map_id == 1 else {}

    records, problems = apply_corrections(
        result.shapes,
        result.seed_groups,
        result.label_map,
        seed_xy,
        annotations.get("corrections", []),
    )

    # criterion (a): resolve, then measure on what is written
    rings_by_id = {tid: rec["polygons"] for tid, rec in records.items()}
    pre_overlap = measure_overlap(rings_by_id)
    rings_by_id = resolve_overlaps(rings_by_id)
    from riskdyn.workbench.overlap import _to_geom  # areas of resolved rings

    territories = []
    for tid in sorted(records):
        rec = records[tid]
        geom = _to_geom(rings_by_id[tid])
        territories.append(
            {
                "territory_id": tid,
                "name": gt_names.get(tid),
                "name_source": edge_source.replace("adjacencies", "seed-claim"),
                "region_id": None,  # filled below
                "polygons": [
                    [[round(x, 1), round(y, 1)] for x, y in ring]
                    for ring in rings_by_id[tid]
                ],
                "centroid": [round(geom.centroid.x, 1), round(geom.centroid.y, 1)]
                if not geom.is_empty
                else list(rec["centroid"]),
                "area_px": int(round(geom.area)),
                "flags": rec["flags"],
                "outline_source": rec["outline_source"],
            }
        )

    name_to_id = {t["name"]: t["territory_id"] for t in territories if t["name"]}
    regions, membership = _region_tables(annotations, name_to_id)
    for t in territories:
        t["region_id"] = membership.get(t["territory_id"])

    bonuses_doc = _bonuses_doc(annotations, regions, map_id)
    rings_tuples = {
        t["territory_id"]: tuple(
            tuple((float(x), float(y)) for x, y in ring) for ring in t["polygons"]
        )
        for t in territories
    }
    graph = build_graph(
        topology, rings_tuples, (summary.width, summary.height), edge_source,
        annotations,
    )
    edge_set = {(e["a"], e["b"]) for e in graph["edges"]}

    verification = annotations.get("verification", {})
    crit_a = check_a_no_overlap(rings_tuples)
    crit_a["overlap_before_resolution_px2"] = pre_overlap["total_px2"]
    crit_b = check_b_all_present(
        territories, summary.num_territories, sorted(gt_names.values())
    )
    crit_c = check_c_no_nonplayable(territories, verification)
    crit_d = check_d_bonuses(
        bonuses_doc, set(gt_names), edge_set, verification
    )
    if problems:
        crit_b["status"] = "fail"
        crit_b.setdefault("problems", []).extend(problems)

    statuses = [c["status"] for c in (crit_a, crit_b, crit_c, crit_d)]
    report = {
        "schema_version": 2,
        "map_id": map_id,
        "map_name": summary.name,
        "criteria": {
            "a_no_overlap": crit_a,
            "b_all_territories": crit_b,
            "c_no_nonplayable": crit_c,
            "d_bonuses_accurate": crit_d,
        },
        "overall": (
            "fail" if "fail" in statuses
            else "unverified" if "unverified" in statuses
            else "pass"
        ),
        "verification": {
            "note": (
                "a map is DONE only when every criterion is pass, which "
                "requires the human sign-offs in annotations.json; a script "
                "having run is not verification"
            ),
            **verification,
        },
        "segmentation": {
            k: result.report.get(k)
            for k in ("seeding", "selection", "warnings", "bijection")
        },
        "provenance": make_provenance(
            "riskdyn.workbench.build",
            "sam+seed-selection+annotations; checks re-run on the artifacts "
            "as written",
            inputs=[
                f"data/raw/map_images/{map_id}.large.jpg",
                str(annotations_path(map_id).relative_to(cat.REPO_ROOT)),
            ],
        ),
    }

    tjson = {
        "schema_version": 2,
        "map_id": map_id,
        "map_name": summary.name,
        "image_size": [summary.width, summary.height],
        "territories": territories,
        "provenance": report["provenance"],
    }
    (out_dir / "territories.json").write_text(json.dumps(tjson, indent=1))
    _write_svg(territories, (summary.width, summary.height), out_dir / "territories.svg")
    (out_dir / "graph.json").write_text(json.dumps(graph, indent=1))
    (out_dir / "bonuses.json").write_text(json.dumps(bonuses_doc, indent=1))
    (out_dir / "report.json").write_text(json.dumps(report, indent=1))

    from riskdyn.segment.loader import load_map_image

    image = load_map_image(
        cat.image_path(map_id), expected_size=(summary.width, summary.height)
    )
    header = (
        f"map {map_id} {summary.name}: a={crit_a['status']} b={crit_b['status']} "
        f"c={crit_c['status']} d={crit_d['status']}"
    )
    _write_overlay(image, territories, header, out_dir / "overlay.png")
    return report


# --------------------------------------------------------------------------
# SVG -> annotations round-trip
# --------------------------------------------------------------------------

_PATH_RE = re.compile(r'<path id="territory-(\d+)" d="([^"]*)"')


def _parse_d(d: str) -> list[list[list[float]]]:
    rings = []
    for sub in re.split(r"\s*M\s+", d):
        sub = sub.strip().rstrip("Z").strip()
        if not sub:
            continue
        pts = []
        for tok in re.split(r"\s*L\s+|\s+", sub):
            if "," in tok:
                x, y = tok.split(",")
                pts.append([float(x), float(y)])
        if len(pts) >= 3:
            rings.append(pts)
    return rings


def sync_from_svg(map_id: int, out_root: pathlib.Path | None = None) -> int:
    """Import hand-edits to territories.svg back into annotations.json.

    Compares each path's outline against territories.json; changed
    territories become ``replace_outline`` corrections (source
    ``manual-svg-edit``) so the edit survives every future rebuild.
    Returns the number of imported edits.
    """
    out_dir = (out_root or PROCESSED_ROOT) / str(map_id)
    svg = (out_dir / "territories.svg").read_text()
    current = json.loads((out_dir / "territories.json").read_text())
    current_polys = {t["territory_id"]: t["polygons"] for t in current["territories"]}

    edits = []
    for m in _PATH_RE.finditer(svg):
        tid = int(m.group(1))
        rings = _parse_d(m.group(2))
        if tid not in current_polys:
            raise ValueError(f"territories.svg has unknown territory id {tid}")
        if rings != current_polys[tid]:
            edits.append((tid, rings))
    if not edits:
        return 0

    path = annotations_path(map_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    annotations = load_annotations(map_id)
    corrections = annotations.setdefault("corrections", [])
    for tid, rings in edits:
        corrections[:] = [
            c
            for c in corrections
            if not (c.get("op") == "replace_outline" and c.get("territory_id") == tid)
        ]
        corrections.append(
            {
                "op": "replace_outline",
                "territory_id": tid,
                "polygons": rings,
                "source": "manual-svg-edit",
            }
        )
    path.write_text(json.dumps(annotations, indent=1))
    return len(edits)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="per-map artifact workbench build")
    ap.add_argument("map_ids", nargs="+", type=int)
    ap.add_argument(
        "--from-svg",
        action="store_true",
        help="first import hand-edits from territories.svg into annotations.json",
    )
    args = ap.parse_args(argv)
    for map_id in args.map_ids:
        if args.from_svg:
            n = sync_from_svg(map_id)
            print(f"map {map_id}: imported {n} outline edit(s) from territories.svg")
        report = build_map(map_id)
        crit = report["criteria"]
        print(
            f"map {map_id} {report['map_name']}: overall={report['overall']}  "
            + "  ".join(f"{k.split('_')[0]}={v['status']}" for k, v in crit.items())
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
