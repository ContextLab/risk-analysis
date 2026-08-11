"""Vector rendering of a map's territories and connections.

D12 distributes maps as raster JPEGs, so this module produces our own vector
representation from the site's territory label coordinates plus the adjacency
graph. Output is resolution-independent (PDF/SVG), which is what the paper's
figure pipeline needs.

Label placement is collision-avoiding: D12's label coordinates were chosen to
sit inside territory shapes on raster artwork, not for a standalone node-link
diagram, so a fixed offset produces overlapping labels in dense regions
(Europe, Australia). Each label is instead placed by deterministic greedy
candidate search: try a fixed sequence of offsets around the node, take the
first that collides with nothing (no other label, no node marker, no edge),
falling back to the least-bad candidate. Labels displaced far from their node
get a thin leader line so the association stays unambiguous. Node positions
themselves are real geography and never move.
"""
from __future__ import annotations

import math
import pathlib

import matplotlib
matplotlib.use("Agg")
# Emit SVG labels as real <text> elements, not path-based glyphs, for editability in Illustrator.
matplotlib.rcParams["svg.fonttype"] = "none"
# Fixed hash salt so SVG element ids (clip paths etc.) are identical across
# processes; without it matplotlib salts ids per-process and byte-identical
# re-renders of the same figure are impossible.
matplotlib.rcParams["svg.hashsalt"] = "riskdyn"
import matplotlib.pyplot as plt  # noqa: E402

from riskdyn.maps.graph import to_graph  # noqa: E402
from riskdyn.maps.model import MapTopology  # noqa: E402

# Region counts run 0-36 across the D12 catalog, so a single 20-colour
# qualitative map is not enough. tab20 + tab20b + tab20c gives 60 distinct
# categorical colours. Do NOT use `.resampled(n)` here: that samples *across*
# a colormap rather than taking its discrete entries, and for small n returns
# near-identical shades (resampled(2) on tab20 yields two similar blues).
REGION_PALETTE = (
    tuple(matplotlib.colormaps["tab20"].colors)
    + tuple(matplotlib.colormaps["tab20b"].colors)
    + tuple(matplotlib.colormaps["tab20c"].colors)
)

_LABEL_FONTSIZE = 7
_NODE_AREA_PT2 = 260  # scatter `s`; rendered circle diameter is sqrt(s) points.

# Candidate search space, in fixed preference order (determinism depends on
# this ordering being static). Radii grow until a collision-free spot exists;
# angles start at "directly above" (the pre-existing style) and fan outward.
_CANDIDATE_RADII_PT = (12.0, 16.0, 21.0, 27.0, 34.0, 42.0, 52.0, 64.0)
_CANDIDATE_ANGLES_DEG = (90, 60, 120, 30, 150, 0, 180, 330, 210, 300, 240, 270)
# Extended tier for maps so dense the primary fan finds no collision-free
# spot: push farther out with a finer angular fan. A label out here always
# gets a leader line, so distance no longer costs legibility.
_EXTENDED_RADII_PT = (44.0, 54.0, 66.0, 80.0, 100.0)
_EXTENDED_ANGLES_DEG = (90, 75, 105, 60, 120, 45, 135, 30, 150, 15, 165, 0,
                        180, 345, 195, 330, 210, 315, 225, 300, 240, 285,
                        255, 270)
# Displacement beyond which a label gets a leader line back to its node.
_LEADER_MIN_RADIUS_PT = 26.0
# Minimum clearance enforced between a label and other labels/nodes/edges.
_LABEL_PAD_PX = 2.0

_HA_SHIFT = {"left": 0.0, "center": 0.5, "right": 1.0}
_VA_SHIFT = {"bottom": 0.0, "center": 0.5, "top": 1.0}


def _rect_overlap_area(a: tuple[float, float, float, float],
                       b: tuple[float, float, float, float]) -> float:
    """Overlap area of two (x0, y0, x1, y1) rects; 0.0 when disjoint."""
    w = min(a[2], b[2]) - max(a[0], b[0])
    if w <= 0.0:
        return 0.0
    h = min(a[3], b[3]) - max(a[1], b[1])
    if h <= 0.0:
        return 0.0
    return w * h


def _segment_intersects_rect(x0: float, y0: float, x1: float, y1: float,
                             rect: tuple[float, float, float, float]) -> bool:
    """Liang-Barsky test: does the segment (x0,y0)-(x1,y1) cross the rect?"""
    dx = x1 - x0
    dy = y1 - y0
    t_min, t_max = 0.0, 1.0
    for p, q in (
        (-dx, x0 - rect[0]),
        (dx, rect[2] - x0),
        (-dy, y0 - rect[1]),
        (dy, rect[3] - y0),
    ):
        if p == 0.0:
            if q < 0.0:
                return False  # parallel and outside
            continue
        t = q / p
        if p < 0.0:
            if t > t_max:
                return False
            t_min = max(t_min, t)
        else:
            if t < t_min:
                return False
            t_max = min(t_max, t)
    return t_min <= t_max


def _place_labels(
    items: list[tuple[float, float, float, float]],
    node_rects: list[tuple[float, float, float, float]],
    segments: list[tuple[float, float, float, float]],
    dpi: float,
) -> list[tuple[float, float, str, str, bool]]:
    """Choose a label position around each node, avoiding collisions.

    Args:
        items: per label (node centre x px, node centre y px, text width px,
               text height px), in placement order.
        node_rects: display-space bounding rects of every node marker.
        segments: display-space edge segments (x0, y0, x1, y1).
        dpi: figure dpi, for converting candidate radii from points to pixels.

    Returns:
        Per label (dx points, dy points, ha, va, needs_leader). Greedy and
        fully deterministic: fixed placement order, fixed candidate order,
        first collision-free candidate wins, ties broken by candidate order.
    """
    px_per_pt = dpi / 72.0
    pad = _LABEL_PAD_PX
    max_reach_px = _EXTENDED_RADII_PT[-1] * px_per_pt

    # Precompute unit vectors and alignments per angle (static across nodes).
    def angle_geometry(angles):
        geom = []
        for ang in angles:
            ux = math.cos(math.radians(ang))
            uy = math.sin(math.radians(ang))
            ha = "left" if ux > 0.35 else ("right" if ux < -0.35 else "center")
            va = "bottom" if uy > 0.35 else ("top" if uy < -0.35 else "center")
            geom.append((ux, uy, ha, va))
        return geom

    # Fixed candidate sequence: primary fan first (near, coarse), then the
    # extended fan (far, fine) for nodes the primary fan cannot serve.
    candidate_seq = [
        (r_pt, geom)
        for radii, geom in (
            (_CANDIDATE_RADII_PT, angle_geometry(_CANDIDATE_ANGLES_DEG)),
            (_EXTENDED_RADII_PT, angle_geometry(_EXTENDED_ANGLES_DEG)),
        )
        for r_pt in radii
    ]

    seg_bboxes = [
        (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        for x0, y0, x1, y1 in segments
    ]

    placed_rects: list[tuple[float, float, float, float]] = []
    results: list[tuple[float, float, str, str, bool]] = []

    for cx, cy, w, h in items:
        # Prefilter to geometry near enough to ever collide with a candidate.
        window = (cx - max_reach_px - w - pad, cy - max_reach_px - h - pad,
                  cx + max_reach_px + w + pad, cy + max_reach_px + h + pad)
        near_nodes = [r for r in node_rects if _rect_overlap_area(r, window) > 0.0]
        near_segs = [
            segments[i] for i, bb in enumerate(seg_bboxes)
            if bb[0] < window[2] and bb[2] > window[0]
            and bb[1] < window[3] and bb[3] > window[1]
        ]

        # Three-tier degradation, all deterministic (fixed candidate order,
        # ties broken by earlier candidate):
        #   1. first candidate touching nothing at all;
        #   2. else the zero-overlap candidate crossed by the fewest edges
        #      (a grid-dense map can leave no edge-free spot anywhere);
        #   3. else the minimum soft score. Labels are never dropped.
        best_score = math.inf
        best = None
        best_zero = None  # least-crossed candidate with zero overlap
        best_zero_crossings = math.inf
        found_clear = False
        for r_pt, geom in candidate_seq:
            r_px = r_pt * px_per_pt
            for a_idx, (ux, uy, ha, va) in enumerate(geom):
                x0 = cx + r_px * ux - w * _HA_SHIFT[ha]
                y0 = cy + r_px * uy - h * _VA_SHIFT[va]
                rect = (x0 - pad, y0 - pad, x0 + w + pad, y0 + h + pad)

                overlap = sum(_rect_overlap_area(rect, r) for r in placed_rects)
                overlap += sum(_rect_overlap_area(rect, r) for r in near_nodes)
                crossings = sum(
                    1 for sx0, sy0, sx1, sy1 in near_segs
                    if _segment_intersects_rect(sx0, sy0, sx1, sy1, rect)
                )
                # A displaced label brings a leader line with it; that line
                # must not be drawn through an already-placed label.
                needs_leader = r_pt >= _LEADER_MIN_RADIUS_PT
                if needs_leader:
                    anchor = (cx + r_px * ux, cy + r_px * uy)
                    crossings += sum(
                        1 for r in placed_rects
                        if _segment_intersects_rect(cx, cy, anchor[0], anchor[1], r)
                    )
                candidate = (r_pt * ux, r_pt * uy, r_pt, ha, va,
                             (x0, y0, x0 + w, y0 + h))
                # Soft score: overlaps dominate, then edge crossings, then a
                # mild preference for staying close in the preferred direction.
                score = (
                    3.0 * overlap
                    + 0.4 * w * h * crossings
                    + 4.0 * (r_pt - _CANDIDATE_RADII_PT[0])
                    + float(a_idx)
                )
                if score < best_score:
                    best_score = score
                    best = candidate
                if overlap == 0.0:
                    if crossings == 0:
                        best_zero = candidate
                        found_clear = True
                        break
                    if crossings < best_zero_crossings:
                        best_zero_crossings = crossings
                        best_zero = candidate
            if found_clear:
                break

        chosen = best_zero if best_zero is not None else best
        assert chosen is not None  # candidate list is never empty
        dx_pt, dy_pt, r_pt, ha, va, tight_rect = chosen
        placed_rects.append(tight_rect)
        leader = r_pt >= _LEADER_MIN_RADIUS_PT
        if leader:
            # Later labels must treat this leader line as an obstacle, exactly
            # like a map edge.
            seg = (cx, cy, cx + dx_pt * px_per_pt, cy + dy_pt * px_per_pt)
            segments.append(seg)
            seg_bboxes.append((min(seg[0], seg[2]), min(seg[1], seg[3]),
                               max(seg[0], seg[2]), max(seg[1], seg[3])))
        results.append((dx_pt, dy_pt, ha, va, leader))
    return results


def _build_figure(
    topology: MapTopology,
    *,
    width: int | None = None,
    height: int | None = None,
    title: str | None = None,
    colour_by_region: bool = True,
):
    """Build the rendered figure; returns (fig, label_artists).

    Split from render_map so tests can inspect the actual rendered label
    extents before the figure is saved. Callers own closing the figure.
    """
    graph = to_graph(topology)

    # D12 uses screen coordinates (y grows downward); invert for plotting.
    # .get(..., 0) rather than [...]: a phantom node (an adjacency
    # referencing an unknown territory id — see check_invariants) carries
    # none of these attributes, and rendering must degrade to a visibly
    # wrong plot rather than crash with a bare KeyError.
    positions = {
        node: (data.get("x", 0), -data.get("y", 0)) for node, data in graph.nodes(data=True)
    }

    region_ids = sorted({data.get("region_id", 0) for _, data in graph.nodes(data=True)})
    colour_for = {rid: REGION_PALETTE[i % len(REGION_PALETTE)]
                  for i, rid in enumerate(region_ids)}

    aspect = (width / height) if (width and height) else 1.5
    fig, ax = plt.subplots(figsize=(12, 12 / aspect))

    for source, target in graph.edges:
        x0, y0 = positions[source]
        x1, y1 = positions[target]
        ax.plot([x0, x1], [y0, y1], color="0.55", linewidth=0.8, zorder=1)

    for node, data in graph.nodes(data=True):
        x, y = positions[node]
        colour = colour_for[data.get("region_id", 0)] if colour_by_region else "white"
        ax.scatter([x], [y], s=_NODE_AREA_PT2, color=colour, edgecolors="black",
                   linewidths=0.8, zorder=2)

    if title:
        ax.set_title(title)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.tight_layout()

    # Finalise the layout so transData and text metrics are the ones the saved
    # figure will use, then place labels against real rendered extents.
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()

    probe = ax.text(0, 0, "", fontsize=_LABEL_FONTSIZE)
    items = []
    labels_text = []
    for node, data in graph.nodes(data=True):
        text = data.get("name") or str(node)
        probe.set_text(text)
        bbox = probe.get_window_extent(renderer=renderer)
        cx, cy = ax.transData.transform(positions[node])
        items.append((cx, cy, bbox.width, bbox.height))
        labels_text.append((node, text))
    probe.remove()

    node_radius_px = (math.sqrt(_NODE_AREA_PT2) / 2.0 + 1.0) * fig.dpi / 72.0
    node_rects = [
        (cx - node_radius_px, cy - node_radius_px,
         cx + node_radius_px, cy + node_radius_px)
        for cx, cy, _, _ in items
    ]
    segments = []
    for source, target in graph.edges:
        sx, sy = ax.transData.transform(positions[source])
        tx, ty = ax.transData.transform(positions[target])
        segments.append((sx, sy, tx, ty))

    placements = _place_labels(items, node_rects, segments, fig.dpi)

    label_artists = []
    for (node, text), (dx, dy, ha, va, leader) in zip(labels_text, placements):
        arrowprops = None
        if leader:
            # Displaced far enough that association is ambiguous: thin leader
            # line back to the node, stopping short of the marker edge.
            # Dotted, not solid: adjacency edges are solid grey lines, and a
            # solid leader reads as a false border (a reader counting a
            # territory's neighbours would miscount). The dash pattern, not
            # hue, carries the distinction so it survives greyscale print.
            arrowprops = dict(arrowstyle="-", linewidth=0.6, color="0.45",
                              linestyle=(0, (1.2, 2.0)), capstyle="round",
                              shrinkA=2, shrinkB=math.sqrt(_NODE_AREA_PT2) / 2.0 + 1.0)
        artist = ax.annotate(
            text, positions[node],
            textcoords="offset points", xytext=(dx, dy),
            ha=ha, va=va, fontsize=_LABEL_FONTSIZE, zorder=3,
            arrowprops=arrowprops,
        )
        label_artists.append(artist)

    return fig, label_artists


def render_map(
    topology: MapTopology,
    out_path: str | pathlib.Path,
    *,
    width: int | None = None,
    height: int | None = None,
    title: str | None = None,
    colour_by_region: bool = True,
) -> pathlib.Path:
    """Render a map to a vector or raster image file.

    Args:
        topology: MapTopology with territory coordinates and adjacency.
        out_path: Output file path (.pdf, .svg, or .png).
        width: Aspect ratio width (not output pixel width); combined with height
               to set figure aspect ratio. Output dimensions are determined by
               content via bbox_inches="tight".
        height: Aspect ratio height (not output pixel height); combined with width
                to set figure aspect ratio.
        title: Optional title for the plot.
        colour_by_region: If True, color nodes by region_id; if False, all white.

    Returns:
        pathlib.Path to the saved file.
    """
    out_path = pathlib.Path(out_path)
    fig, _ = _build_figure(
        topology, width=width, height=height, title=title,
        colour_by_region=colour_by_region,
    )
    # Strip the embedded timestamp so re-rendering the same map produces
    # byte-identical output (figures are regenerated; diffs must stay clean).
    suffix = out_path.suffix.lower()
    if suffix == ".svg":
        metadata = {"Date": None}
    elif suffix == ".pdf":
        metadata = {"CreationDate": None}
    else:
        metadata = None
    fig.savefig(out_path, transparent=True, bbox_inches="tight", metadata=metadata)
    plt.close(fig)
    return out_path
