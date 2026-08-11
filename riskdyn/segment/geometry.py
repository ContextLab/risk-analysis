"""Label map -> territory polygons, centroids, areas, and SVG output."""
from __future__ import annotations

import pathlib
from dataclasses import dataclass

import cv2
import numpy as np


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
