"""Label map -> territory polygons, centroids, areas, and SVG output."""
from __future__ import annotations

import pathlib
from dataclasses import dataclass

import cv2
import numpy as np


def claim_coastal_margin(label_map: np.ndarray, buffer_px: int) -> np.ndarray:
    """Assign near-shore background pixels to the nearest territory.

    D12 prints territory names and army markers in the water beside small
    territories (islands especially), and the ground-truth label anchors sit
    there too.  Faithful coastline polygons therefore cannot contain those
    anchors.  This claims every background pixel within ``buffer_px`` of a
    territory for the *nearest* territory -- no merging, contested water
    splits at the midline, and open ocean beyond the margin stays
    background.  Deterministic for a given label map.
    """
    if buffer_px <= 0:
        return label_map
    fg = label_map > 0
    if not fg.any():
        return label_map
    src = (~fg).astype(np.uint8)  # zero at territory pixels
    dist, nearest = cv2.distanceTransformWithLabels(
        src, cv2.DIST_L2, 3, labelType=cv2.DIST_LABEL_PIXEL
    )
    # DIST_LABEL_PIXEL: every zero pixel gets a unique id; map id -> label.
    lookup = np.zeros(int(nearest.max()) + 1, dtype=np.int32)
    ys, xs = np.nonzero(fg)
    lookup[nearest[ys, xs]] = label_map[ys, xs]
    out = label_map.copy()
    claim = (~fg) & (dist <= buffer_px)
    out[claim] = lookup[nearest[claim]]
    return out


def close_label_gaps(
    label_map: np.ndarray,
    image: np.ndarray,
    gap_px: int,
    land_px: int,
    land_dist: float,
) -> np.ndarray:
    """Close sub-stroke gaps and land-coloured coverage holes.

    SAM masks stop a few pixels short of the drawn border and coastline
    strokes, leaving those stroke pixels unclaimed; label anchors printed on
    text glyphs near an edge then fall just outside every polygon.  Two
    claims, both to the *nearest* label:

    - any unclaimed pixel within ``gap_px`` (sub-stroke-width -- this is the
      drawn line itself and its anti-aliasing, not open water);
    - any unclaimed pixel within ``land_px`` whose colour is at least
      ``land_dist`` from the background colour.  The threshold sits above
      the coastal glow (RGB dist 45-90 from open water on World Classic) so
      this can never re-add a water halo -- it only fills genuine coverage
      holes on decisively land-coloured pixels (e.g. Eastern US, 10 px).

    Claimed pixels go to the euclidean-nearest label.  Gap-tier pixels that
    two labels both reach within ``gap_px`` are CONTESTED -- the pixel sits
    on a shared border stroke or in a narrow strait, and claiming it would
    be a guess (D12 prints Mongolia's anchor 1.0 px from Irkutsk's mask and
    1.4 px from Mongolia's) -- so they stay unclaimed.  This also keeps the
    gap closing from bridging narrow straits, which would manufacture false
    adjacencies downstream.  Land-tier pixels are exempt from the contested
    test (a genuine coverage hole may straddle two labels' reach).
    Deterministic for a given label map and image.
    """
    if gap_px <= 0 and land_px <= 0:
        return label_map
    fg = label_map > 0
    bg = ~fg
    if not fg.any() or not bg.any():
        return label_map
    bg_color = np.median(image[bg], axis=0)
    dist, nearest = cv2.distanceTransformWithLabels(
        bg.astype(np.uint8), cv2.DIST_L2, 3, labelType=cv2.DIST_LABEL_PIXEL
    )
    lookup = np.zeros(int(nearest.max()) + 1, dtype=np.int32)
    ys, xs = np.nonzero(fg)
    lookup[nearest[ys, xs]] = label_map[ys, xs]
    d_bg = np.linalg.norm(image.astype(np.float32) - bg_color, axis=-1)
    h, w = label_map.shape
    r = int(gap_px)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    counts = np.zeros((h, w), dtype=np.uint16)
    for label in range(1, int(label_map.max()) + 1):
        m = label_map == label
        if not m.any():
            continue
        lys, lxs = np.nonzero(m)
        y0 = max(int(lys.min()) - r, 0); y1 = min(int(lys.max()) + r + 1, h)
        x0 = max(int(lxs.min()) - r, 0); x1 = min(int(lxs.max()) + r + 1, w)
        grown = cv2.dilate(m[y0:y1, x0:x1].astype(np.uint8), kernel)
        counts[y0:y1, x0:x1] += grown
    contested = counts >= 2
    claim = bg & (
        ((dist <= gap_px) & ~contested)
        | ((dist <= land_px) & (d_bg >= land_dist))
    )
    if not claim.any():
        return label_map
    out = label_map.copy()
    out[claim] = lookup[nearest[claim]]
    return out


@dataclass(frozen=True)
class TerritoryShape:
    index: int                      # 1-based, stable ordering (see below)
    polygon: tuple[tuple[float, float], ...]  # simplified outline, image px
    centroid: tuple[float, float]   # pixel-mass centroid
    area_px: int                    # pixel count, not polygon area
    flags: tuple[str, ...] = ()     # human-review flags


def extract_territories(label_map: np.ndarray) -> list[TerritoryShape]:
    """Vectorize each label into one simplified polygon.

    Territories are re-indexed deterministically by reading order of their
    centroids (top-to-bottom, left-to-right, on a 16 px row grid) so the same
    label map always yields the same indices regardless of paint order.
    """
    shapes = []
    for label in range(1, int(label_map.max()) + 1):
        mask = (label_map == label).astype(np.uint8)
        area = int(mask.sum())
        if area == 0:
            continue
        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        if not contours:
            continue
        contour = max(contours, key=cv2.contourArea)
        peri = cv2.arcLength(contour, True)
        eps = max(1.0, 0.0015 * peri)
        approx = cv2.approxPolyDP(contour, eps, True)
        flags = []
        if len(approx) < 3:
            approx = contour  # degenerate simplification; keep raw outline
            flags.append("degenerate-simplification")
        poly = tuple((float(p[0][0]), float(p[0][1])) for p in approx)
        ys, xs = np.nonzero(mask)
        centroid = (float(xs.mean()), float(ys.mean()))
        shapes.append(
            TerritoryShape(0, poly, centroid, area, tuple(flags))
        )
    shapes.sort(key=lambda s: (round(s.centroid[1] / 16), round(s.centroid[0]), s.area_px))
    return [
        TerritoryShape(i, s.polygon, s.centroid, s.area_px, s.flags)
        for i, s in enumerate(shapes, start=1)
    ]


def polygons_containing(
    shapes: list[TerritoryShape], x: float, y: float
) -> list[int]:
    """Indices of territories whose polygon contains (x, y) (edges count)."""
    hits = []
    for s in shapes:
        cnt = np.array(s.polygon, dtype=np.float32).reshape(-1, 1, 2)
        if cv2.pointPolygonTest(cnt, (float(x), float(y)), False) >= 0:
            hits.append(s.index)
    return hits


def _path_d(polygon: tuple[tuple[float, float], ...]) -> str:
    pts = [f"{x:.1f},{y:.1f}" for x, y in polygon]
    return "M " + " L ".join(pts) + " Z"


def write_svg(
    shapes: list[TerritoryShape],
    size: tuple[int, int],
    path: str | pathlib.Path,
) -> None:
    """Emit territories.svg: one <path> per territory with id/centroid/area."""
    w, h = size
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}">',
    ]
    for s in shapes:
        cx, cy = s.centroid
        lines.append(
            f'  <path id="territory-{s.index}" d="{_path_d(s.polygon)}" '
            f'data-centroid="{cx:.1f},{cy:.1f}" data-area-px="{s.area_px}" '
            f'fill="none" stroke="black" stroke-width="1"/>'
        )
    lines.append("</svg>")
    pathlib.Path(path).write_text("\n".join(lines) + "\n")
