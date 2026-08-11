"""Filter and merge raw SAM masks into a territory label map.

SAM's automatic generator returns a soup of masks at several granularities:
whole-image, whole-ocean, whole-continent, individual territories, and
sub-territory fragments (text, icons).  This module reduces that soup to a
single int32 label map where 0 is background/ocean and 1..N are territory
candidates.

The rules, in order:
  1. size gates -- drop masks that are implausibly small or large;
  2. frame/border gates -- drop ring-shaped masks and masks that own most of
     the image border (ocean, decorative frames);
  3. duplicate suppression -- IoU above a threshold keeps the higher-scoring
     mask;
  4. composite suppression -- a mask mostly covered by >= 2 smaller kept
     masks is a merged region (a continent), not a territory;
  5. painting -- remaining masks are rasterized largest-first so that finer
     masks overwrite coarser ones; each label then keeps only its largest
     connected component, and remnants that lost most of their pixels to
     finer masks are dropped entirely.

Everything is deterministic: input masks arrive in a deterministic order and
every rule is a pure function of that order.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from riskdyn.segment.sam import RawMask


@dataclass(frozen=True)
class CandidateParams:
    min_area_frac: float = 0.0003   # of image area
    min_area_px: int = 250
    max_area_frac: float = 0.25     # of image area
    dup_iou: float = 0.85
    frame_bbox_frac: float = 0.75   # bbox spans this much of both dims ...
    frame_fill_max: float = 0.35    # ... but fills less than this of the bbox
    border_own_frac: float = 0.30   # owns this much of the image border
    child_containment: float = 0.85  # child area inside parent
    composite_cover: float = 0.55   # children cover this much of parent
    remnant_keep_frac: float = 0.45  # painted survivors must keep this much


def fill_holes(mask: np.ndarray) -> np.ndarray:
    """Fill interior holes (e.g. text) of a boolean mask."""
    h, w = mask.shape
    inv = (~mask).astype(np.uint8)
    ff = inv.copy()
    ff_mask = np.zeros((h + 2, w + 2), np.uint8)
    cv2.floodFill(ff, ff_mask, (0, 0), 0)
    # flood from all four corners to be safe with border-touching masks
    for seed in ((w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        if inv[seed[1], seed[0]] and ff[seed[1], seed[0]]:
            cv2.floodFill(ff, ff_mask, seed, 0)
    return mask | (ff > 0)


def _iou(a: RawMask, b: RawMask) -> float:
    ax0, ay0, ax1, ay1 = a.bbox
    bx0, by0, bx1, by1 = b.bbox
    if ax1 <= bx0 or bx1 <= ax0 or ay1 <= by0 or by1 <= ay0:
        return 0.0
    inter = int(np.logical_and(a.mask, b.mask).sum())
    if inter == 0:
        return 0.0
    return inter / (a.area + b.area - inter)


def _contained_frac(child: RawMask, parent: RawMask) -> float:
    """Fraction of child's area lying inside parent."""
    cx0, cy0, cx1, cy1 = child.bbox
    px0, py0, px1, py1 = parent.bbox
    if cx1 <= px0 or px1 <= cx0 or cy1 <= py0 or py1 <= cy0:
        return 0.0
    inter = int(np.logical_and(child.mask, parent.mask).sum())
    return inter / max(child.area, 1)


def select_masks(
    raw: list[RawMask],
    image_shape: tuple[int, int],
    params: CandidateParams | None = None,
) -> tuple[list[RawMask], list[str]]:
    """Apply rules 1-4.  Returns (kept masks, warnings)."""
    p = params or CandidateParams()
    h, w = image_shape
    image_area = h * w
    min_area = max(p.min_area_px, int(p.min_area_frac * image_area))
    max_area = int(p.max_area_frac * image_area)
    border_len = 2 * (h + w)
    warnings: list[str] = []

    sized = []
    for r in raw:
        if r.area < min_area or r.area > max_area:
            continue
        x0, y0, x1, y1 = r.bbox
        bw, bh = x1 - x0, y1 - y0
        fill = r.area / max(bw * bh, 1)
        if (
            bw >= p.frame_bbox_frac * w
            and bh >= p.frame_bbox_frac * h
            and fill < p.frame_fill_max
        ):
            continue  # ring / frame / scattered decoration
        border_own = (
            int(r.mask[0, :].sum())
            + int(r.mask[-1, :].sum())
            + int(r.mask[:, 0].sum())
            + int(r.mask[:, -1].sum())
        )
        if border_own > p.border_own_frac * border_len:
            continue  # owns most of the border: ocean or frame
        sized.append(r)

    # Duplicate suppression: prefer higher score, then the deterministic
    # arrival order (area desc, bbox) as tiebreak.
    by_pref = sorted(
        range(len(sized)), key=lambda i: (-sized[i].score, i)
    )
    kept_idx: list[int] = []
    for i in by_pref:
        if all(_iou(sized[i], sized[j]) < p.dup_iou for j in kept_idx):
            kept_idx.append(i)
    dedup = [sized[i] for i in sorted(kept_idx)]  # restore area-desc order

    # Composite suppression, largest first so nested composites unwind.
    keep_flags = [True] * len(dedup)
    for i, parent in enumerate(dedup):
        if not keep_flags[i]:
            continue
        union: np.ndarray | None = None
        n_children = 0
        for j, child in enumerate(dedup):
            if j == i or not keep_flags[j]:
                continue
            if child.area >= parent.area:
                continue
            if _contained_frac(child, parent) >= p.child_containment:
                n_children += 1
                cm = np.logical_and(child.mask, parent.mask)
                union = cm if union is None else np.logical_or(union, cm)
        if union is not None and n_children >= 2:
            if int(union.sum()) >= p.composite_cover * parent.area:
                keep_flags[i] = False
    kept = [m for m, f in zip(dedup, keep_flags) if f]

    if not kept:
        warnings.append("no masks survived filtering")
    return kept, warnings


def paint_label_map(
    kept: list[RawMask],
    image_shape: tuple[int, int],
    params: CandidateParams | None = None,
) -> tuple[np.ndarray, list[str]]:
    """Apply rule 5.  Returns (int32 label map with labels 1..N, warnings).

    Kept masks are painted in arrival order (area desc), holes filled, so
    smaller masks overwrite larger ones.  Labels are then compacted: for each
    label only the largest connected component survives, and a label whose
    surviving pixels fall below ``remnant_keep_frac`` of its original mask is
    removed altogether (it was a coarse mask almost fully claimed by finer
    ones).
    """
    p = params or CandidateParams()
    h, w = image_shape
    label_map = np.zeros((h, w), dtype=np.int32)
    for idx, r in enumerate(kept, start=1):
        label_map[fill_holes(r.mask)] = idx

    warnings: list[str] = []
    out = np.zeros_like(label_map)
    next_label = 1
    min_area = max(p.min_area_px, int(p.min_area_frac * h * w))
    for idx, r in enumerate(kept, start=1):
        painted = label_map == idx
        n_painted = int(painted.sum())
        if n_painted < min_area or n_painted < p.remnant_keep_frac * r.area:
            continue
        n, comp = cv2.connectedComponents(painted.astype(np.uint8), connectivity=4)
        if n <= 1:
            continue
        sizes = np.bincount(comp.ravel())
        sizes[0] = 0
        best = int(sizes.argmax())
        if sizes[best] < min_area:
            continue
        out[comp == best] = next_label
        next_label += 1
    if next_label == 1:
        warnings.append("label map is empty after painting")
    return out, warnings
