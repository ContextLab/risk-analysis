"""Filter and merge raw SAM masks into a territory label map.

SAM's automatic generator returns a soup of masks at several granularities:
whole-image, whole-ocean, whole-continent, individual territories, and
sub-territory detail (name pills, icons, legend contents).  This module
reduces that soup to a single int32 label map where 0 is background/ocean
and 1..N are territory candidates.

The rules, in order:
  1. size gates -- drop masks that are implausibly small or large;
  2. frame/border gates -- drop ring-shaped masks spanning the image and
     masks that own most of the image border (ocean, decorative frames);
  3. duplicate suppression -- IoU above a threshold keeps the higher score;
  4. inset-box removal -- a highly rectangular mask containing several other
     masks is a legend/inset; the box and its contents are dropped;
  5. composite suppression -- a mask mostly covered by >= 2 smaller kept
     masks is a merged region (a continent), not a territory;
  6. containment resolution -- when one kept mask sits inside another:
     comparable areas mean the same territory found twice (keep the higher
     score); a much smaller child is internal detail like a printed name
     pill (drop the child);
  7. ocean-blob removal -- a mask whose colour matches the uncovered
     background and whose surroundings are background is a patch of ocean;
  8. painting -- survivors are rasterized largest-first so finer masks
     overwrite coarser ones; each label keeps its largest connected
     component, and remnants that lost most of their pixels are dropped.

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
    min_area_frac: float = 0.00015  # of image area
    min_area_px: int = 120
    max_area_frac: float = 0.25     # of image area
    dup_iou: float = 0.85
    frame_bbox_frac: float = 0.75   # bbox spans this much of both dims ...
    frame_fill_max: float = 0.35    # ... but fills less than this of the bbox
    border_own_frac: float = 0.30   # owns this much of the image border
    inset_rect_fill: float = 0.85   # bbox fill above which a mask is a "box"
    inset_max_area_frac: float = 0.15
    inset_min_children: int = 2
    child_containment: float = 0.85  # child area inside parent
    composite_cover: float = 0.55   # children cover this much of parent
    pill_max_frac: float = 0.08     # contained detail is at most this much of parent
    pill_min_aspect: float = 1.8    # ... and wide-short like a printed name
    pill_max_height: int = 40       # ... at most two text lines tall
    ocean_color_dist: float = 30.0  # RGB distance: "background-like"
    ocean_exact_dist: float = 10.0  # RGB distance: "is literally the water"
    ocean_ring_frac: float = 0.60   # surrounded-by-open-background fraction
    ocean_bg_erosion: int = 7       # erode background so thin border-line
    #   gaps between adjacent territories do not count as "open water"
    ocean_small_exempt_frac: float = 0.005  # background-LIKE masks smaller
    #   than this fraction of the image are never dropped: islands whose
    #   palette resembles the water (World Classic's Britain, dist 14)
    ocean_exact_ring_frac: float = 0.45  # exact-water masks are dropped on
    #   much weaker evidence of isolation ...
    ocean_exact_border_frac: float = 0.05  # ... or if they own real border
    ocean_exact_big_frac: float = 0.03  # ... or are simply huge
    remnant_keep_frac: float = 0.45  # painted survivors must keep this much
    fringe_margin: float = 15.0     # how decisively ocean-like a fringe pixel must be
    fringe_keep_min: float = 0.60   # never trim a mask below this fraction
    coastal_buffer_px: int = 14     # near-shore water claimed by nearest territory


def fill_holes(mask: np.ndarray) -> np.ndarray:
    """Fill interior holes (e.g. text) of a boolean mask."""
    h, w = mask.shape
    inv = (~mask).astype(np.uint8)
    ff = inv.copy()
    ff_mask = np.zeros((h + 2, w + 2), np.uint8)
    for seed in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        if inv[seed[1], seed[0]] and ff[seed[1], seed[0]]:
            cv2.floodFill(ff, ff_mask, seed, 0)
    return mask | (ff > 0)


def _bbox_overlap(a: RawMask, b: RawMask) -> bool:
    ax0, ay0, ax1, ay1 = a.bbox
    bx0, by0, bx1, by1 = b.bbox
    return not (ax1 <= bx0 or bx1 <= ax0 or ay1 <= by0 or by1 <= ay0)


def _intersection(a: RawMask, b: RawMask) -> int:
    if not _bbox_overlap(a, b):
        return 0
    x0 = min(a.bbox[0], b.bbox[0]); y0 = min(a.bbox[1], b.bbox[1])
    x1 = max(a.bbox[2], b.bbox[2]); y1 = max(a.bbox[3], b.bbox[3])
    return int(
        np.logical_and(a.mask[y0:y1, x0:x1], b.mask[y0:y1, x0:x1]).sum()
    )


def _iou(a: RawMask, b: RawMask) -> float:
    inter = _intersection(a, b)
    return inter / (a.area + b.area - inter) if inter else 0.0


def select_masks(
    raw: list[RawMask],
    image_shape: tuple[int, int],
    params: CandidateParams | None = None,
    image: np.ndarray | None = None,
) -> tuple[list[RawMask], list[str]]:
    """Apply rules 1-7.  Returns (kept masks, warnings).

    ``image`` (RGB uint8) enables the ocean-blob rule; without it that rule
    is skipped.
    """
    p = params or CandidateParams()
    h, w = image_shape
    image_area = h * w
    min_area = max(p.min_area_px, int(p.min_area_frac * image_area))
    max_area = int(p.max_area_frac * image_area)
    border_len = 2 * (h + w)
    warnings: list[str] = []

    # -- rules 1-2: size, frame, border ------------------------------------
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

    # -- rule 3: near-identical duplicates ---------------------------------
    by_pref = sorted(range(len(sized)), key=lambda i: (-sized[i].score, i))
    kept_idx: list[int] = []
    for i in by_pref:
        if all(_iou(sized[i], sized[j]) < p.dup_iou for j in kept_idx):
            kept_idx.append(i)
    masks = [sized[i] for i in sorted(kept_idx)]  # restore area-desc order

    # -- rule 4: legend / inset boxes --------------------------------------
    drop = set()
    for i, box in enumerate(masks):
        x0, y0, x1, y1 = box.bbox
        fill = box.area / max((x1 - x0) * (y1 - y0), 1)
        if fill < p.inset_rect_fill or box.area > p.inset_max_area_frac * image_area:
            continue
        children = [
            j
            for j, m in enumerate(masks)
            if j != i
            and m.area < box.area
            and _intersection(m, box) >= p.child_containment * m.area
        ]
        if len(children) >= p.inset_min_children:
            drop.add(i)
            drop.update(children)
            warnings.append(
                f"dropped inset box at {box.bbox} with {len(children)} contents"
            )
    masks = [m for i, m in enumerate(masks) if i not in drop]

    # -- rule 5: composites (continents) -----------------------------------
    keep_flags = [True] * len(masks)
    for i, parent in enumerate(masks):
        if not keep_flags[i]:
            continue
        union: np.ndarray | None = None
        n_children = 0
        for j, child in enumerate(masks):
            if j == i or not keep_flags[j] or child.area >= parent.area:
                continue
            if _intersection(child, parent) >= p.child_containment * child.area:
                n_children += 1
                cm = np.logical_and(child.mask, parent.mask)
                union = cm if union is None else np.logical_or(union, cm)
        if union is not None and n_children >= 2:
            if int(union.sum()) >= p.composite_cover * parent.area:
                keep_flags[i] = False
    masks = [m for m, f in zip(masks, keep_flags) if f]

    # -- rule 6: printed-name pills ----------------------------------------
    # Genuine overlaps (a merged two-territory mask plus one of its halves,
    # or two near-duplicates below the IoU gate) are NOT resolved here: the
    # painting step lets the finer mask overwrite the coarser one and then
    # discards coarse remnants, which handles both cases correctly.  The one
    # thing painting gets wrong is a territory's printed name: the name mask
    # would carve a pill-shaped hole and claim the label anchor.  Name pills
    # are recognisable -- small relative to the territory that contains
    # them, wide-short like a line or two of text -- and dropped.
    keep_flags = [True] * len(masks)
    for i, big in enumerate(masks):
        if not keep_flags[i]:
            continue
        for j, small in enumerate(masks):
            if i == j or not keep_flags[j]:
                continue
            if small.area >= p.pill_max_frac * big.area:
                continue
            if _intersection(big, small) < p.child_containment * small.area:
                continue
            x0, y0, x1, y1 = small.bbox
            bw, bh = x1 - x0, y1 - y0
            if bh <= p.pill_max_height and bw >= p.pill_min_aspect * bh:
                keep_flags[j] = False
    masks = [m for m, f in zip(masks, keep_flags) if f]

    # -- rule 7: ocean blobs -----------------------------------------------
    if image is not None and masks:
        covered = np.zeros((h, w), dtype=bool)
        for m in masks:
            covered |= m.mask
        background = ~covered
        if background.any():
            bg_color = np.median(image[background], axis=0)
            # Only *open* background counts as water: eroding removes the
            # thin unclaimed slivers along drawn border lines, which would
            # otherwise make every bordered territory look ocean-surrounded
            # (fatal on near-monochrome maps like Arctic Circle).
            k = p.ocean_bg_erosion
            bg_core = cv2.erode(
                background.astype(np.uint8), np.ones((k, k), np.uint8)
            ).astype(bool)
            # The ring is an annulus 5..12 px out from the mask: far enough
            # that it clears both the mask itself (bg_core erodes 3-4 px
            # around every mask) and thin border-line gaps, close enough to
            # sample the mask's true surroundings.
            inner_k = np.ones((11, 11), np.uint8)
            outer_k = np.ones((25, 25), np.uint8)
            small_exempt = p.ocean_small_exempt_frac * image_area
            keep_flags = [True] * len(masks)
            for i, m in enumerate(masks):
                dist = float(
                    np.linalg.norm(np.median(image[m.mask], axis=0) - bg_color)
                )
                if dist >= p.ocean_color_dist:
                    continue
                exact = dist < p.ocean_exact_dist
                if not exact and m.area < small_exempt:
                    continue  # small bg-LIKE mask: plausibly an island
                m8 = m.mask.astype(np.uint8)
                ring = cv2.dilate(m8, outer_k).astype(bool) & ~cv2.dilate(
                    m8, inner_k
                ).astype(bool)
                n_ring = max(int(ring.sum()), 1)
                ring_bg = int((ring & bg_core).sum()) / n_ring
                border_own = (
                    int(m.mask[0, :].sum())
                    + int(m.mask[-1, :].sum())
                    + int(m.mask[:, 0].sum())
                    + int(m.mask[:, -1].sum())
                ) / border_len
                if exact:
                    # Literally water-coloured.  SAM tiles big oceans into
                    # chunks whose rings are walled in by sibling chunks and
                    # coastlines, so isolation evidence is weakened and
                    # border ownership / sheer size also convict.
                    drop = (
                        ring_bg > p.ocean_exact_ring_frac
                        or border_own > p.ocean_exact_border_frac
                        or m.area > p.ocean_exact_big_frac * image_area
                    )
                else:
                    drop = ring_bg > p.ocean_ring_frac
                if drop:
                    keep_flags[i] = False
                    warnings.append(
                        f"dropped background-coloured blob at {m.bbox} "
                        f"(dist {dist:.0f}, ring_bg {ring_bg:.2f}, "
                        f"border {border_own:.2f})"
                    )
            masks = [m for m, f in zip(masks, keep_flags) if f]

    if not masks:
        warnings.append("no masks survived filtering")
    return masks, warnings


def _trim_ocean_fringe(
    kept: list[RawMask],
    image: np.ndarray,
    p: CandidateParams,
) -> list[np.ndarray]:
    """Strip water-coloured aura pixels from each mask's coastline.

    SAM masks routinely include the soft glow the artwork paints around
    coastlines.  That aura pushes polygons out into the water and lets a big
    territory's halo outcompete a nearby island for the water between them.
    A pixel is trimmed when its colour is decisively closer to the global
    background colour than to the mask's own core colour.  Masks whose core
    already looks like the background (e.g. dark-blue Europe on a dark-blue
    ocean) are left untouched -- there is no signal to trim on -- as are
    masks the trim would reduce below ``fringe_keep_min``.
    """
    covered = np.zeros(image.shape[:2], dtype=bool)
    for m in kept:
        covered |= m.mask
    background = ~covered
    if not background.any():
        return [m.mask for m in kept]
    bg_color = np.median(image[background], axis=0)

    out = []
    for m in kept:
        x0, y0, x1, y1 = m.bbox
        sub = image[y0:y1, x0:x1].astype(np.float32)
        msk = m.mask[y0:y1, x0:x1]
        core_color = np.median(sub[msk], axis=0)
        if np.linalg.norm(core_color - bg_color) < 2 * p.fringe_margin:
            out.append(m.mask)
            continue
        d_bg = np.linalg.norm(sub - bg_color, axis=-1)
        d_core = np.linalg.norm(sub - core_color, axis=-1)
        keep = msk & ~(d_bg + p.fringe_margin < d_core)
        if int(keep.sum()) < p.fringe_keep_min * m.area:
            out.append(m.mask)
            continue
        full = np.zeros_like(m.mask)
        full[y0:y1, x0:x1] = keep
        out.append(full)
    return out


def paint_label_map(
    kept: list[RawMask],
    image_shape: tuple[int, int],
    params: CandidateParams | None = None,
    image: np.ndarray | None = None,
) -> tuple[np.ndarray, list[str]]:
    """Apply rule 8.  Returns (int32 label map with labels 1..N, warnings).

    Kept masks are painted in arrival order (area desc), holes filled, so
    smaller masks overwrite larger ones.  When ``image`` is given, each
    mask's water-coloured fringe is trimmed first (see _trim_ocean_fringe).
    Labels are then compacted: for each label only the largest connected
    component survives, and a label whose surviving pixels fall below
    ``remnant_keep_frac`` of its original mask is removed altogether (it was
    a coarse mask almost fully claimed by finer ones).
    """
    p = params or CandidateParams()
    h, w = image_shape
    if image is not None and kept:
        pixel_masks = _trim_ocean_fringe(kept, image, p)
    else:
        pixel_masks = [r.mask for r in kept]
    label_map = np.zeros((h, w), dtype=np.int32)
    for idx, mask in enumerate(pixel_masks, start=1):
        label_map[fill_holes(mask)] = idx

    warnings: list[str] = []
    out = np.zeros_like(label_map)
    next_label = 1
    min_area = max(p.min_area_px, int(p.min_area_frac * h * w))
    for idx, mask in enumerate(pixel_masks, start=1):
        own_area = max(int(mask.sum()), 1)
        painted = label_map == idx
        n_painted = int(painted.sum())
        if n_painted < min_area or n_painted < p.remnant_keep_frac * own_area:
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
