"""Graphs-only per-map build: authored annotations (schema v2) -> artifacts.

The segmentation-free path of plan v4 (issue #4): territory OUTLINES are
deferred, so this builder needs only

    data/authored/maps/<id>/annotations.json   (schema_version 2)
    data/raw/map_images/<id>.large.jpg         (overlay background only)
    data/raw/map_catalog.json                  (expected counts + image size)

and writes to ``data/processed/maps/<id>/``:

    nodes.json    -- one node per territory (id, name, x, y, region_ids,
                     source).  Territory-to-region is many-to-many: a
                     territory may sit in 0, 1, or several regions (map 7's
                     city bonuses overlap the colour regions), so membership
                     is always a LIST of region ids; [] means no region.
    graph.json    -- undirected edge list with kind/wraps/rule/status,
                     degree stats, and a ``connected`` flag (a disconnected
                     board is legal on some maps: reported, never a failure)
    bonuses.json  -- per-region values + extra bonuses + special rules,
                     same shape as the segmentation build's bonuses.json
    report.json   -- plan v4 criteria (b, e, f, d), each pass/fail/
                     unverified; a script having run is never a pass
    overlay.png   -- artwork with edges drawn under labeled nodes, the
                     human verification artifact.  LOCAL ONLY: the artwork
                     is D12's and must never be republished/committed.

Two orthogonal per-edge statuses:

- ``status`` -- does the edge EXIST?  For edges sourced from D12's own
  markup this is ``confirmed``; hand-proposed edges stay ``proposed``
  until verified against the artwork.
- ``kind_status`` -- is the border-vs-route CLASSIFICATION verified?
  Geometry can only propose a kind (plan v3 review: precision plateaus at
  0.90), so a kind stays ``proposed`` even on a confirmed edge.

Validation fails loudly and never silently repairs: duplicate ids or
normalized names, unknown edge endpoints, self-loops, duplicate edges,
unknown region ids, and out-of-bounds coordinates all abort the build.

CLI:  ./.venv/bin/python -m riskdyn.workbench.graph_build <map_id>
"""
from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any

from riskdyn.segment import catalog as cat
from riskdyn.workbench.build import AUTHORED_ROOT, PROCESSED_ROOT, _bonuses_doc
from riskdyn.workbench.provenance import make_provenance
from riskdyn.workbench.score import normalize_name

EDGE_KINDS = {"shared-border", "route", "unknown"}
EDGE_STATUSES = {"proposed", "confirmed"}


# --------------------------------------------------------------------------
# loading + validation
# --------------------------------------------------------------------------

def load_annotations_v2(path: pathlib.Path, map_id: int) -> dict:
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} not found: the graphs-only build has no automated "
            "fallback; author the schema-v2 annotations first"
        )
    doc = json.loads(path.read_text())
    if doc.get("schema_version") != 2:
        raise ValueError(
            f"{path} has schema_version {doc.get('schema_version')!r}; the "
            "graphs-only build requires schema_version 2 (authored "
            "territories + edges lists)"
        )
    if doc.get("map_id") != map_id:
        raise ValueError(f"{path} declares map_id {doc.get('map_id')}, expected {map_id}")
    for key in ("territories", "edges"):
        if not isinstance(doc.get(key), list):
            raise ValueError(f"{path}: {key!r} must be a list in schema v2")
    return doc


def validate_annotations(doc: dict, width: int, height: int) -> None:
    """All structural problems at once, as one loud ValueError.

    Never repairs anything: a bad authored file is a bad authored file.
    """
    problems: list[str] = []
    territories = doc["territories"]
    region_ids = {r["region_id"] for r in doc.get("regions", [])}

    seen_ids: set[int] = set()
    seen_names: dict[str, int] = {}
    for t in territories:
        tid = t.get("territory_id")
        if tid in seen_ids:
            problems.append(f"duplicate territory_id {tid}")
        seen_ids.add(tid)
        norm = normalize_name(t.get("name") or "")
        if norm in seen_names:
            problems.append(
                f"duplicate normalized name {norm!r}: territories "
                f"{seen_names[norm]} and {tid}"
            )
        else:
            seen_names[norm] = tid
        if "region_id" in t:
            # Never coerce the retired scalar: a silent [rid] conversion is
            # exactly how a wrong region assignment would survive unnoticed.
            problems.append(
                f"territory {tid} uses the retired scalar 'region_id' key; "
                "territory-to-region is many-to-many -- re-author it as a "
                "'region_ids' list ([] means no region)"
            )
        rids = t.get("region_ids")
        if not isinstance(rids, list):
            problems.append(
                f"territory {tid} has no 'region_ids' list; membership must "
                "be a list of region ids ([] means no region)"
            )
        else:
            if len(set(rids)) != len(rids):
                problems.append(
                    f"territory {tid} lists a region more than once in "
                    f"region_ids {rids}"
                )
            for rid in rids:
                if rid not in region_ids:
                    problems.append(
                        f"territory {tid} has region_id {rid} not present "
                        "in regions"
                    )
        x, y = t.get("x"), t.get("y")
        if not (0 <= x < width and 0 <= y < height):
            problems.append(
                f"territory {tid} at ({x}, {y}) outside image bounds "
                f"{width}x{height}"
            )

    seen_edges: set[tuple[int, int]] = set()
    for e in doc["edges"]:
        a, b = e.get("a"), e.get("b")
        if a == b:
            problems.append(f"self-loop edge {a}-{b}")
            continue
        unknown = [v for v in (a, b) if v not in seen_ids]
        if unknown:
            problems.append(f"edge {a}-{b} references unknown territory_id {unknown}")
            continue
        key = (min(a, b), max(a, b))
        if key in seen_edges:
            problems.append(f"duplicate undirected edge {key}")
        seen_edges.add(key)
        if e.get("kind", "unknown") not in EDGE_KINDS:
            problems.append(f"edge {key} has unknown kind {e.get('kind')!r}")
        if e.get("status") not in EDGE_STATUSES:
            problems.append(f"edge {key} has invalid status {e.get('status')!r}")

    for c in doc.get("edge_confirmations", []):
        key = (min(c["a"], c["b"]), max(c["a"], c["b"]))
        if key not in seen_edges:
            problems.append(f"edge confirmation for non-existent edge {key}")

    if problems:
        raise ValueError(
            f"annotations for map {doc.get('map_id')} are invalid "
            f"({len(problems)} problem(s)):\n  " + "\n  ".join(problems)
        )


# --------------------------------------------------------------------------
# graph assembly
# --------------------------------------------------------------------------

def _human_signoff(conf: dict | None) -> bool:
    """True only for an explicit human sign-off; agents never count."""
    conf = conf or {}
    by = str(conf.get("by") or "").strip()
    return (
        bool(conf.get("verified"))
        and bool(by)
        and "agent" not in by.lower()
        and not by.lower().startswith("claude")
    )


def _edge_records(doc: dict) -> list[dict]:
    """Canonicalized (a < b) edge records with confirmations applied."""
    confirmations = {
        (min(c["a"], c["b"]), max(c["a"], c["b"])): c
        for c in doc.get("edge_confirmations", [])
    }
    records = []
    for e in doc["edges"]:
        a, b = min(e["a"], e["b"]), max(e["a"], e["b"])
        rec = {
            "a": a,
            "b": b,
            "kind": e.get("kind", "unknown"),
            "kind_status": e.get("kind_status", e["status"]),
            "wraps": bool(e.get("wraps", False)),
            "rule": e.get("rule"),
            "status": e["status"],
            "source": e.get("source", "unknown"),
            "note": e.get("note", ""),
        }
        conf = confirmations.get((a, b))
        if conf:
            rec["kind"] = conf["kind"]
            rec["kind_status"] = "confirmed"
            rec["kind_source"] = f"confirmed:{conf.get('by', 'unknown')}"
            if conf.get("rule") is not None:
                rec["rule"] = conf["rule"]
        records.append(rec)
    return sorted(records, key=lambda r: (r["a"], r["b"]))


def _degrees(node_ids: list[int], edges: list[dict]) -> dict[int, int]:
    deg = {tid: 0 for tid in node_ids}
    for e in edges:
        deg[e["a"]] += 1
        deg[e["b"]] += 1
    return deg


def _connected_components(node_ids: list[int], edges: list[dict]) -> list[set[int]]:
    adj: dict[int, set[int]] = {tid: set() for tid in node_ids}
    for e in edges:
        adj[e["a"]].add(e["b"])
        adj[e["b"]].add(e["a"])
    seen: set[int] = set()
    components: list[set[int]] = []
    for start in node_ids:
        if start in seen:
            continue
        comp, stack = {start}, [start]
        while stack:
            for n in adj[stack.pop()] - comp:
                comp.add(n)
                stack.append(n)
        seen |= comp
        components.append(comp)
    return components


# --------------------------------------------------------------------------
# plan v4 criteria (b, e, f, d)
# --------------------------------------------------------------------------

def _crit_b(territories: list[dict], expected: int) -> dict[str, Any]:
    names = [t.get("name") for t in territories]
    unnamed = sum(1 for n in names if not n)
    named = [n for n in names if n]
    duplicated = sorted({n for n in named if named.count(n) > 1})
    ok = len(territories) == expected and not unnamed and not duplicated
    return {
        "criterion": "b: every playable territory has exactly one node, correctly named",
        "status": "pass" if ok else "fail",
        "expected_count": expected,
        "found_count": len(territories),
        "unnamed_territories": unnamed,
        "duplicated_names": duplicated,
        "note": (
            "names come from the authored source recorded per node "
            "(nodes.json); count equality alone would not be evidence"
        ),
    }


def _crit_e(edges: list[dict]) -> dict[str, Any]:
    proposed = [(e["a"], e["b"]) for e in edges if e["status"] != "confirmed"]
    kinds_proposed = sum(1 for e in edges if e["kind_status"] != "confirmed")
    return {
        "criterion": "e: the graph is correct (every edge confirmed)",
        "status": "pass" if not proposed else "unverified",
        "n_edges": len(edges),
        "n_confirmed": len(edges) - len(proposed),
        "proposed_edges": proposed,
        "n_kind_unconfirmed": kinds_proposed,
        "note": (
            "status covers edge EXISTENCE; the border-vs-route "
            "classification is tracked separately per edge as kind_status "
            "and does not gate this criterion"
        ),
    }


def _crit_f(
    territories: list[dict], regions: list[dict], expected_regions: int
) -> dict[str, Any]:
    """Region membership is many-to-many: pass when the distinct region ids
    used across all territories' region_ids match the catalog's num_regions
    AND every region defined in ``regions`` is used.  Territories with
    ``region_ids: []`` are legal (map 7 has grey territories in no region):
    their count is reported for the human to check, never a failure.  When
    ``regions`` is empty the regions are genuinely absent, so f is fail, not
    unverified."""
    used: set[int] = set()
    for t in territories:
        used.update(t["region_ids"])
    defined = {r["region_id"] for r in regions}
    unused_defined = sorted(defined - used)
    multi = [t["territory_id"] for t in territories if len(t["region_ids"]) > 1]
    none = [t["territory_id"] for t in territories if not t["region_ids"]]
    ok = len(used) == expected_regions and not unused_defined
    return {
        "criterion": "f: region membership is correct",
        "status": "pass" if ok else "fail",
        "expected_regions": expected_regions,
        "distinct_regions_used": len(used),
        "defined_regions_unused": unused_defined,
        "n_territories_multiple_regions": len(multi),
        "territories_multiple_regions": multi,
        "n_territories_no_region": len(none),
        "territories_no_region": none,
        "note": (
            "membership is many-to-many; a territory in no region is legal "
            "-- the multiple/none counts are what a human checks against "
            "the artwork"
        ),
    }


def _crit_d(
    bonuses_doc: dict, regions: list[dict], verification: dict | None
) -> dict[str, Any]:
    values = {
        e["region_id"]: e["value"]
        for e in bonuses_doc.get("bonuses", [])
        if e.get("kind") == "region" and e.get("region_id") is not None
    }
    missing = [
        r["region_id"]
        for r in regions
        if values.get(r["region_id"]) is None
    ]
    conf = (verification or {}).get("bonuses_confirmed") or {}
    human_ok = _human_signoff(conf)
    if missing:
        status = "fail"
    elif human_ok:
        status = "pass"
    else:
        status = "unverified"
    return {
        "criterion": "d: bonuses and special rules are accurate",
        "status": status,
        "regions_without_bonus": missing,
        "n_extra_bonuses": len(
            [e for e in bonuses_doc.get("bonuses", []) if e.get("kind") != "region"]
        ),
        "n_special_rules": len(bonuses_doc.get("special_rules", [])),
        "human_confirmation": (
            {"confirmed": True, "by": conf.get("by"), "at": conf.get("at")}
            if human_ok
            else {
                "confirmed": False,
                "needs": (
                    "a human must check bonuses.json against the artwork and "
                    "confirm via annotations.json verification."
                    "bonuses_confirmed; an agent transcription is not sign-off"
                ),
            }
        ),
    }


# --------------------------------------------------------------------------
# overlay
# --------------------------------------------------------------------------

_KIND_LINESTYLE = {"shared-border": "-", "route": "--", "unknown": ":"}
_STATUS_COLOR = {"confirmed": "#00e5ff", "proposed": "#ff9500"}


def _wrap_stub_segments(
    xa: float, ya: float, xb: float, yb: float, w: float
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """A wrapping edge as two stubs, one from each node to its map border.

    Never a line straight across the image: the edge exits one side and
    re-enters the other, meeting the seam at the interpolated y.
    """
    shift = min((-w, w), key=lambda s: abs(xa - (xb + s)))
    xb_shifted = xb + shift
    seam_a = w if shift > 0 else 0.0  # x where the a-side stub leaves the map
    seam_b = 0.0 if shift > 0 else w
    t = (seam_a - xa) / (xb_shifted - xa) if xb_shifted != xa else 0.5
    y_cross = ya + t * (yb - ya)
    return [((xa, ya), (seam_a, y_cross)), ((xb, yb), (seam_b, y_cross))]


def _write_overlay(
    image, territories: list[dict], edges: list[dict], header: str,
    size: tuple[int, int], path: pathlib.Path,
) -> None:
    """Edges drawn UNDER labeled nodes: an edge that should not exist (or a
    missing one) must be visible at a glance -- this is the artifact the
    human verifies.  Solid = shared-border, dashed = route, dotted =
    unknown kind; orange = proposed, cyan = confirmed edge existence.

    Nodes are coloured by their FIRST region id (membership is many-to-many;
    overlapping membership gets no visual encoding of its own -- the title
    reports how many territories sit in multiple or zero regions).  A
    territory in no region stays white."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    w, h = size
    fig, ax = plt.subplots(figsize=(w / 72, h / 72), dpi=144)
    ax.imshow(image)
    pos = {t["territory_id"]: (float(t["x"]), float(t["y"])) for t in territories}
    first_rids = sorted(
        {t["region_ids"][0] for t in territories if t["region_ids"]}
    )
    cmap = plt.get_cmap("tab20")
    region_color = {rid: cmap(i % 20) for i, rid in enumerate(first_rids)}
    for e in edges:
        (xa, ya), (xb, yb) = pos[e["a"]], pos[e["b"]]
        style = _KIND_LINESTYLE[e["kind"]]
        color = _STATUS_COLOR[e["status"]]
        segments = (
            _wrap_stub_segments(xa, ya, xb, yb, float(w))
            if e["wraps"]
            else [((xa, ya), (xb, yb))]
        )
        for (x0, y0), (x1, y1) in segments:
            ax.plot([x0, x1], [y0, y1], style, color=color, linewidth=1.6,
                    zorder=2)
    for t in territories:
        x, y = pos[t["territory_id"]]
        color = (
            region_color[t["region_ids"][0]] if t["region_ids"] else "#ffffff"
        )
        ax.plot([x], [y], "o", color=color, markeredgecolor="black",
                markersize=4, zorder=3)
        ax.text(
            x, y - 6, t["name"], color="white", fontsize=5, ha="center",
            va="bottom", zorder=4,
            bbox=dict(facecolor="black", alpha=0.55, pad=0.6, lw=0),
        )
    ax.set_title(
        header + "   [solid=shared-border  dashed=route  dotted=unknown  "
        "orange=proposed]", fontsize=8,
    )
    ax.set_xlim(0, w)
    ax.set_ylim(h, 0)
    ax.axis("off")
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


# --------------------------------------------------------------------------
# the build
# --------------------------------------------------------------------------

def build_graph_map(
    map_id: int,
    out_root: pathlib.Path | None = None,
    authored_root: pathlib.Path | None = None,
) -> dict:
    """Graphs-only build for one map; returns the report dict."""
    ann_path = (authored_root or AUTHORED_ROOT) / str(map_id) / "annotations.json"
    out_dir = (out_root or PROCESSED_ROOT) / str(map_id)
    summary = cat.load_catalog()[map_id]
    doc = load_annotations_v2(ann_path, map_id)
    validate_annotations(doc, summary.width, summary.height)
    out_dir.mkdir(parents=True, exist_ok=True)

    territories = sorted(doc["territories"], key=lambda t: t["territory_id"])
    edges = _edge_records(doc)
    node_ids = [t["territory_id"] for t in territories]
    degrees = _degrees(node_ids, edges)
    components = _connected_components(node_ids, edges)

    try:
        ann_rel = str(ann_path.relative_to(cat.REPO_ROOT))
    except ValueError:
        ann_rel = str(ann_path)
    provenance = make_provenance(
        "riskdyn.workbench.graph_build",
        "authored annotations schema v2; nodes and edges carried verbatim, "
        "never invented or repaired",
        inputs=[ann_rel],
    )

    nodes_doc = {
        "schema_version": 1,
        "map_id": map_id,
        "map_name": summary.name,
        "image_size": [summary.width, summary.height],
        "nodes": [
            {
                "territory_id": t["territory_id"],
                "name": t["name"],
                "x": t["x"],
                "y": t["y"],
                "region_ids": list(t["region_ids"]),
                "source": t.get("source", "unknown"),
                "confidence": t.get("confidence", "low"),
            }
            for t in territories
        ],
        "provenance": provenance,
    }

    deg_values = [degrees[tid] for tid in node_ids] or [0]
    graph_doc = {
        "schema_version": 2,
        "map_id": map_id,
        "map_name": summary.name,
        "nodes": node_ids,
        "n_edges": len(edges),
        "n_confirmed": sum(1 for e in edges if e["status"] == "confirmed"),
        "n_kind_confirmed": sum(1 for e in edges if e["kind_status"] == "confirmed"),
        "wrap": {"horizontal": any(e["wraps"] for e in edges), "vertical": False},
        "edge_source": sorted({e["source"] for e in edges}),
        "connected": len(components) == 1,
        "n_components": len(components),
        "degree": {
            "min": min(deg_values),
            "max": max(deg_values),
            "mean": round(sum(deg_values) / len(deg_values), 3),
        },
        "edges": edges,
        "provenance": provenance,
    }

    region_defs = []
    for r in doc.get("regions", []):
        region_defs.append(
            {
                "region_id": r["region_id"],
                "name": r.get("name"),
                "territory_ids": sorted(
                    t["territory_id"]
                    for t in territories
                    if r["region_id"] in t["region_ids"]
                ),
            }
        )
    bonuses_doc = _bonuses_doc(doc, region_defs, map_id)
    bonuses_doc["special_rules"] = doc.get("special_rules", [])
    bonuses_doc["provenance"]["produced_by"] = "riskdyn.workbench.graph_build"
    bonuses_doc["provenance"]["inputs"] = [ann_rel]

    verification = doc.get("verification", {})
    crit_b = _crit_b(territories, summary.num_territories)
    crit_e = _crit_e(edges)
    crit_f = _crit_f(territories, doc.get("regions", []), summary.num_regions)
    crit_d = _crit_d(bonuses_doc, doc.get("regions", []), verification)
    statuses = [c["status"] for c in (crit_b, crit_e, crit_f, crit_d)]
    report = {
        "schema_version": 1,
        "map_id": map_id,
        "map_name": summary.name,
        "pipeline": "graphs-only (plan v4); territory outlines deferred",
        "criteria": {
            "b_all_territories": crit_b,
            "e_graph_confirmed": crit_e,
            "f_regions": crit_f,
            "d_bonuses_accurate": crit_d,
        },
        "connected": len(components) == 1,
        "overall": (
            "fail" if "fail" in statuses
            else "unverified" if "unverified" in statuses
            else "pass"
        ),
        "verification": {
            "note": (
                "a map is DONE only when every criterion is pass, which "
                "requires the human sign-offs in annotations.json; a script "
                "(or an agent) having run is not verification"
            ),
            **verification,
        },
        "provenance": provenance,
    }

    (out_dir / "nodes.json").write_text(json.dumps(nodes_doc, indent=1))
    (out_dir / "graph.json").write_text(json.dumps(graph_doc, indent=1))
    (out_dir / "bonuses.json").write_text(json.dumps(bonuses_doc, indent=1))
    (out_dir / "report.json").write_text(json.dumps(report, indent=1))

    from riskdyn.segment.loader import load_map_image

    image = load_map_image(
        cat.image_path(map_id), expected_size=(summary.width, summary.height)
    )
    header = (
        f"map {map_id} {summary.name}: b={crit_b['status']} "
        f"e={crit_e['status']} f={crit_f['status']} d={crit_d['status']}  "
        f"({len(edges)} edges, "
        f"{'connected' if graph_doc['connected'] else f'{len(components)} components'}; "
        f"{crit_f['n_territories_multiple_regions']} terr in multiple regions, "
        f"{crit_f['n_territories_no_region']} in none)"
    )
    _write_overlay(
        image, territories, edges, header,
        (summary.width, summary.height), out_dir / "overlay.png",
    )
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="graphs-only per-map build from schema-v2 annotations"
    )
    ap.add_argument("map_ids", nargs="+", type=int)
    args = ap.parse_args(argv)
    for map_id in args.map_ids:
        report = build_graph_map(map_id)
        crit = report["criteria"]
        print(
            f"map {map_id} {report['map_name']}: overall={report['overall']}  "
            + "  ".join(f"{k.split('_')[0]}={v['status']}" for k, v in crit.items())
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
