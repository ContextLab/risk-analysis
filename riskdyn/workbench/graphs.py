"""graph.json: adjacency with per-edge kind, wrap, and provenance.

The edge LIST comes from an authoritative source (for map 1, D12's own
``data-adjacencies`` markup in the fixture).  Edge KIND (shared-border vs
route) is NOT derivable from geometry with acceptable precision -- measured
on map 1, precision plateaus at 0.90 with overlapping distance
distributions (plan v3 adversarial review) -- so geometry here only
PROPOSES a kind per known-true edge from the measured pixel gap, and every
edge stays ``status: "proposed"`` until a vision pass or human confirms it
via annotations.  Nothing in this module invents or removes edges.

Cylindrical wrap is handled by measuring each gap at horizontal shifts
{-w, 0, +w} (map 1's Alaska-Kamchatka is 801 px direct but wraps).
"""
from __future__ import annotations

import numpy as np

from riskdyn.maps.model import MapTopology
from riskdyn.workbench.overlap import Ring
from riskdyn.workbench.provenance import make_provenance

# Measured on map 1 (plan v3 review): border-ish gaps cluster <= ~4 px
# (SAM masks stop short of the drawn stroke), route-ish gaps >= 12 px,
# with NO edges between 4 and 12 px.  Edges landing in the dead band get
# kind "unknown" rather than a guess.
BORDER_MAX_PX = 6.0
ROUTE_MIN_PX = 12.0


def undirected_edges(topology: MapTopology) -> list[tuple[int, int]]:
    """Sorted unique undirected edges; raises on asymmetric adjacency."""
    ids = {t.territory_id for t in topology.territories}
    adj = {t.territory_id: set(t.adjacencies) for t in topology.territories}
    for tid, neigh in adj.items():
        unknown = neigh - ids
        if unknown:
            raise ValueError(f"territory {tid} adjacent to unknown ids {sorted(unknown)}")
        for n in neigh:
            if tid not in adj[n]:
                raise ValueError(f"asymmetric adjacency: {tid}->{n} but not {n}->{tid}")
    return sorted({(min(a, b), max(a, b)) for a, ns in adj.items() for b in ns})


def _boundary_points(rings: tuple[Ring, ...], size: tuple[int, int]) -> np.ndarray:
    """Rasterized boundary pixel coordinates (N, 2) as (x, y) floats."""
    import cv2

    w, h = size
    mask = np.zeros((h, w), dtype=np.uint8)
    for ring in rings:
        if len(ring) >= 3:
            pts = np.array(ring, dtype=np.float32).round().astype(np.int32)
            cv2.fillPoly(mask, [pts.reshape(-1, 1, 2)], 1)
    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return np.zeros((0, 2), dtype=np.float32)
    return np.concatenate([c.reshape(-1, 2) for c in contours]).astype(np.float32)


def measure_edge_gaps(
    edges: list[tuple[int, int]],
    rings_by_id: dict[int, tuple[Ring, ...]],
    size: tuple[int, int],
) -> dict[tuple[int, int], dict]:
    """Per edge: min boundary-to-boundary gap in px, direct and wrapped.

    Returns ``{(a, b): {"gap_px", "gap_direct_px", "wraps"}}``.  ``wraps``
    means the cylindrical (shifted) distance is strictly smaller than the
    direct one.  Edges whose territories have no geometry get gap None.
    """
    pts = {tid: _boundary_points(r, size) for tid, r in rings_by_id.items()}
    w = float(size[0])
    out: dict[tuple[int, int], dict] = {}
    for a, b in edges:
        pa, pb = pts.get(a), pts.get(b)
        if pa is None or pb is None or not len(pa) or not len(pb):
            out[(a, b)] = {"gap_px": None, "gap_direct_px": None, "wraps": False}
            continue
        best = {}
        for shift in (-w, 0.0, w):
            shifted = pa + np.array([shift, 0.0], dtype=np.float32)
            d = np.sqrt(
                ((shifted[:, None, :] - pb[None, :, :]) ** 2).sum(-1)
            ).min()
            best[shift] = float(d)
        direct = best[0.0]
        wrapped = min(best[-w], best[w])
        out[(a, b)] = {
            "gap_px": round(min(direct, wrapped), 2),
            "gap_direct_px": round(direct, 2),
            "wraps": wrapped < direct,
        }
    return out


def propose_kind(gap_px: float | None) -> str:
    if gap_px is None:
        return "unknown"
    if gap_px <= BORDER_MAX_PX:
        return "shared-border"
    if gap_px >= ROUTE_MIN_PX:
        return "route"
    return "unknown"


def build_graph(
    topology: MapTopology,
    rings_by_id: dict[int, tuple[Ring, ...]],
    size: tuple[int, int],
    edge_source: str,
    annotations: dict | None = None,
) -> dict:
    """Assemble graph.json content.

    ``annotations["edges"]["confirmations"]`` entries
    (``{"a", "b", "kind", "by", "at", "rule"?, "one_way"?}``) override the
    geometric proposal and flip the edge to ``status: "confirmed"``.  A
    confirmation for a non-existent edge is an error (typo guard).
    """
    edges = undirected_edges(topology)
    gaps = measure_edge_gaps(edges, rings_by_id, size)

    confirmations: dict[tuple[int, int], dict] = {}
    ann_edges = (annotations or {}).get("edges", {})
    for c in ann_edges.get("confirmations", []):
        key = (min(c["a"], c["b"]), max(c["a"], c["b"]))
        if key not in set(edges):
            raise ValueError(f"confirmation for non-existent edge {key}")
        confirmations[key] = c

    edge_records = []
    for a, b in edges:
        g = gaps[(a, b)]
        conf = confirmations.get((a, b))
        record = {
            "a": a,
            "b": b,
            "kind": conf["kind"] if conf else propose_kind(g["gap_px"]),
            "kind_source": (
                f"confirmed:{conf.get('by', 'unknown')}" if conf else "geometry-proposal"
            ),
            "status": "confirmed" if conf else "proposed",
            "gap_px": g["gap_px"],
            "wraps": bool(g["wraps"]),
            "one_way": bool(conf.get("one_way", False)) if conf else False,
            "rule": conf.get("rule") if conf else None,
        }
        edge_records.append(record)

    n_confirmed = sum(1 for e in edge_records if e["status"] == "confirmed")
    return {
        "schema_version": 1,
        "map_id": topology.map_id,
        "nodes": sorted(t.territory_id for t in topology.territories),
        "n_edges": len(edge_records),
        "n_confirmed": n_confirmed,
        "wrap": {"horizontal": any(e["wraps"] for e in edge_records), "vertical": False},
        "edge_source": edge_source,
        "edges": edge_records,
        "provenance": make_provenance(
            "riskdyn.workbench.graphs.build_graph",
            "edge list from "
            + edge_source
            + "; edge kind = geometry proposal unless confirmed via annotations",
        ),
    }
