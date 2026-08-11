"""Criterion (a): territory outlines must not overlap.

``measure_overlap`` is the check -- total pairwise intersection area over
the polygons AS WRITTEN to the artifact (the same simplified outlines that
land in territories.json/svg, not some intermediate raster that cannot
overlap by construction).

``resolve_overlaps`` is the fix -- polygon simplification (approxPolyDP in
the extraction stage) lets neighbouring outlines cross by a few px; each
territory's geometry gets everything already assigned to lower territory
ids subtracted, in deterministic ascending-id order, so the measured
overlap of the emitted polygons is exactly zero.  Geometry is allowed to be
approximate (plan v3), so the subtraction slivers are acceptable; pieces
under ``min_piece_px2`` are dropped as simplification noise, EXCEPT that a
territory's largest piece always survives so no territory can vanish.
"""
from __future__ import annotations

from shapely.geometry import MultiPolygon, Polygon
from shapely.validation import make_valid

Ring = tuple[tuple[float, float], ...]


def _to_geom(polygons: tuple[Ring, ...] | list[Ring]):
    """Union of a territory's rings as a valid (multi)polygon."""
    geoms = []
    for ring in polygons:
        if len(ring) < 3:
            continue
        geoms.append(make_valid(Polygon(ring)))
    if not geoms:
        return Polygon()
    out = geoms[0]
    for g in geoms[1:]:
        out = out.union(g)
    return out


def _to_rings(geom, min_piece_px2: float) -> list[Ring]:
    """Exterior rings of a geometry, largest first.

    Interior rings (holes) are NOT representable in the artifact's ring
    format; the caller must re-measure after conversion, which is why
    ``resolve_overlaps`` iterates to a fixed point instead of trusting one
    subtraction pass.
    """
    if geom.is_empty:
        return []

    def polygons_of(g):
        # difference/make_valid may return GeometryCollection mixing
        # polygons with slivers-as-lines; unpack recursively.
        if isinstance(g, Polygon):
            return [g]
        if hasattr(g, "geoms"):
            return [p for part in g.geoms for p in polygons_of(part)]
        return []

    polys = [p for p in polygons_of(geom) if p.area > 0]
    polys.sort(key=lambda p: p.area, reverse=True)
    kept = [p for p in polys if p.area >= min_piece_px2]
    if not kept and polys:
        kept = [polys[0]]  # largest piece always survives
    return [tuple((float(x), float(y)) for x, y in p.exterior.coords[:-1]) for p in kept]


def measure_overlap(
    polygon_sets: dict[int, tuple[Ring, ...]],
) -> dict:
    """Total pairwise intersection area (px^2) between territories.

    Args:
        polygon_sets: territory id -> rings as written to the artifact.

    Returns dict with ``total_px2`` and per-pair ``pairs`` sorted worst
    first.  Self-intersections within one territory's own rings are not
    counted (a territory may not overlap OTHERS; its own rings union).
    """
    ids = sorted(polygon_sets)
    geoms = {i: _to_geom(polygon_sets[i]) for i in ids}
    pairs = []
    total = 0.0
    for idx, a in enumerate(ids):
        ga = geoms[a]
        if ga.is_empty:
            continue
        for b in ids[idx + 1 :]:
            gb = geoms[b]
            if gb.is_empty or not ga.intersects(gb):
                continue
            area = float(ga.intersection(gb).area)
            if area > 0.0:
                total += area
                pairs.append({"a": a, "b": b, "area_px2": round(area, 3)})
    pairs.sort(key=lambda p: -p["area_px2"])
    return {"total_px2": round(total, 3), "n_pairs": len(pairs), "pairs": pairs}


def _quantize(rings: list[Ring] | tuple[Ring, ...], decimals: int) -> tuple[Ring, ...]:
    out = []
    for ring in rings:
        q = tuple((round(float(x), decimals), round(float(y), decimals)) for x, y in ring)
        # collapse consecutive duplicates introduced by rounding
        dedup = [p for i, p in enumerate(q) if p != q[i - 1]]
        if len(dedup) >= 3:
            out.append(tuple(dedup))
    return tuple(out)


def resolve_overlaps(
    polygon_sets: dict[int, tuple[Ring, ...]],
    min_piece_px2: float = 1.0,
    max_rounds: int = 8,
    decimals: int = 1,
) -> dict[int, tuple[Ring, ...]]:
    """Return polygon sets with zero measured mutual overlap.

    Deterministic: ascending territory id wins contested area.  Every ring
    is quantized to ``decimals`` (the artifact's coordinate precision)
    INSIDE the loop, so the zero-overlap fixed point is measured on exactly
    the coordinates that get written -- resolving at full precision and
    rounding afterwards can reintroduce overlap.  Because the ring format
    also cannot express holes, one subtraction pass can reintroduce tiny
    overlaps when a hole is dropped; iterate to a fixed point and raise if
    it is not reached (never silently ship an overlapping artifact).
    """
    current = {
        i: _quantize(rings, decimals) for i, rings in polygon_sets.items()
    }
    for round_no in range(max_rounds):
        if measure_overlap(current)["total_px2"] == 0.0:
            return current
        # Round 0 subtracts exactly; if quantization keeps pushing vertices
        # back across the boundary, later rounds subtract a sub-quantum
        # margin (1.5 * 10^-decimals, i.e. 0.15 px at the default) so the
        # rounded result cannot re-cross.  Erosion is bounded by that
        # margin, well inside plan v3's "geometry may be approximate".
        eps = 0.0 if round_no == 0 else 1.5 * 10.0 ** (-decimals)
        out: dict[int, tuple[Ring, ...]] = {}
        claimed = None
        for tid in sorted(current):
            geom = _to_geom(current[tid])
            if claimed is not None:
                geom = geom.difference(
                    claimed.buffer(eps) if eps else claimed
                )
            rings = _quantize(_to_rings(geom, min_piece_px2), decimals)
            if not rings and current[tid]:
                # A territory entirely swallowed by lower ids is a real
                # extraction defect, not something to paper over silently.
                raise RuntimeError(
                    f"territory {tid} vanished during overlap resolution; "
                    "its geometry is fully contained in lower-id territories"
                )
            out[tid] = tuple(rings)
            solid = _to_geom(rings)  # what the ring format actually encodes
            claimed = solid if claimed is None else claimed.union(solid)
        current = out
    residual = measure_overlap(current)
    if residual["total_px2"] > 0.0:
        raise RuntimeError(
            f"overlap resolution did not converge: {residual['total_px2']} px^2 "
            f"remain across {residual['n_pairs']} pairs"
        )
    return current
