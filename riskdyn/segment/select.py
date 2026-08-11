"""Seed-driven mask selection: claim SAM masks for territory seeds.

A *seed* is a point believed to lie on or near a territory: a D12 render
anchor (``seed_kind="render"``, available for map 1 only, from the test
fixture) or a printed-label centroid (``seed_kind="text"``, supplied by the
text-detection stage for all maps).  Seeds are ALWAYS passed in; this module
never detects them.

Measured before design (2026-08-11, World Classic, 42 ground-truth anchors
against the 283-mask size-gated SAM pool):

- 36/42 anchors sit inside SEVERAL masks (continent composites contain almost
  every anchor at distance 0), 5 inside none, 1 inside only an 89%-water
  strip.  Plain containment and plain proximity both discriminate nothing.
- After structural exclusions (masks containing a seed, water-heavy masks,
  text-pill-shaped masks) the correct mask is the nearest eligible one for
  34 of the 38 anchors whose correct mask exists at all, provided ties at
  distance 0 (near-duplicate masks) break to the tightest area.
- Verified displacements of D12 render anchors from their own artwork:
  1.0-13.6 px, including anchors printed in open ocean (Britain, at the tail
  of the "Iceland" ocean text), on a border stroke (Mongolia), and on the
  WRONG landmass (Iceland's anchor is on Greenland).
- Britain's anchor is nearer to the Iceland island mask (8.1) than to
  Britain's own (13.6); Iceland's anchor is 12.5 from the island.  Greedy
  nearest-first therefore mis-assigns; leftover seeds need max-cardinality
  minimum-total-distance matching.
- Some territories (e.g. Eastern US) have NO own mask anywhere in the soup
  and exist only as the remnant of a composite minus its claimed siblings.

The algorithm, in phases (each deterministic; input masks arrive in the
deterministic order produced by :mod:`riskdyn.segment.sam`):

1. **contained** -- a seed claims the tightest mask containing that seed and
   no other seed.  Text-pill-shaped masks are claimed only as a last resort
   for render seeds and never for text seeds (a text seed always sits inside
   its own printed-name mask).
2. **remnant** -- a seed claims the connected piece of a containing
   composite mask after subtracting all already-claimed pixels, if the piece
   contains no other unmatched seed, is big enough, and is not water.
   Iterated to fixpoint: each claim can unblock a neighbour's remnant.
3. **proximity** -- leftover seeds are matched to unclaimed masks that
   contain no seed at all (max-cardinality, then minimum total distance,
   then lexicographic seed order), one edge per round so each claim informs
   the next remnant fixpoint.  Distances are capped.
4. **merge** -- seeds still unmatched that share a containing mask with only
   other unmatched seeds claim it jointly and are reported as an UNRESOLVED
   MERGE (Greenland/Iceland would land here if the island mask were absent);
   nothing is invented to split them.
5. **attach** -- optional many-to-one accretion: unclaimed masks containing
   no seed attach to the colour-consistent nearest claimed territory
   (archipelagos: Indonesia is several separate island masks).

Selected masks all trace back to a seed, so anchorless output is impossible
by construction; every non-selected mask is reported in aggregate and every
unmatched seed individually.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import cv2
import numpy as np

from riskdyn.segment.sam import RawMask


@dataclass(frozen=True)
class Seed:
    """A territory seed point in image pixel coordinates."""

    seed_id: int
    x: int
    y: int
    name: str = ""


@dataclass(frozen=True)
class SelectParams:
    seed_kind: str = "render"        # "render" (D12 anchors) | "text" (label centroids)
    min_area_px: int = 120           # same floor the candidate filter uses
    min_area_frac: float = 0.00015
    max_area_frac: float = 0.25
    water_px_dist: float = 35.0      # constants shared with candidates.py
    water_frac_max: float = 0.60
    water_ambiguous_frac: float = 0.25
    seed_disk_r: int = 6             # land-colour reference sampled around seeds
    pill_max_height: int = 40        # text-pill shape, as in candidates.py
    pill_min_aspect: float = 1.8
    max_claim_dist: float | None = None  # None -> max(16, 0.013 * image diagonal);
    #   16 px covers every displacement observed on the one ground-truth map
    #   (1.0-13.6 px) with margin while staying below inter-label spacing.
    #   NOT yet validated for text seeds -- revisit when the detector lands.
    attach_orphans: bool = True
    attach_max_dist: float = 12.0    # orphan island -> territory accretion reach
    attach_color_dist: float = 40.0  # median-colour agreement required to attach
    dup_covered_frac: float = 0.5    # orphan mostly covered by claims = duplicate
    proximity_max_candidates: int = 8  # per-seed cap for the exact matching


@dataclass(frozen=True)
class Claim:
    seed_id: int
    mask_indices: tuple[int, ...]    # indices into the input mask list; a
    #   remnant claim has the parent's index here but its OWN pixel mask
    provenance: str                  # contained | remnant | proximity | merged
    distance: float                  # seed -> claimed pixels (0 if inside)
    shared_with: tuple[int, ...] = ()  # other seeds on the same mask (merged)
    flags: tuple[str, ...] = ()


@dataclass
class SelectionResult:
    claims: dict[int, Claim]              # seed_id -> claim
    masks: dict[int, np.ndarray]          # seed_id -> bool pixel mask (its claim)
    unmatched: list[tuple[int, str]]      # (seed_id, reason), seed order
    merges: list[tuple[int, ...]]         # groups of seeds sharing one mask
    attached: dict[int, tuple[int, ...]]  # seed_id -> extra mask indices
    n_input_masks: int = 0
    n_pool: int = 0
    n_rejected_no_seed: int = 0           # pool masks left unselected
    warnings: list[str] = field(default_factory=list)

    def groups(self) -> list[tuple[int, ...]]:
        """Seed ids grouped by shared claim, deterministic order."""
        merged: dict[int, tuple[int, ...]] = {}
        for g in self.merges:
            for s in g:
                merged[s] = g
        out, seen = [], set()
        for sid in sorted(self.claims):
            if sid in seen:
                continue
            g = merged.get(sid, (sid,))
            out.append(g)
            seen.update(g)
        return out


def _water_frac(image: np.ndarray, mask: np.ndarray, bg: np.ndarray,
                dist: float) -> float:
    d = np.linalg.norm(image[mask].astype(np.float32) - bg, axis=1)
    return float((d < dist).mean()) if d.size else 1.0


def _mask_dist(mask_nz: tuple[np.ndarray, np.ndarray], x: int, y: int) -> float:
    ys, xs = mask_nz
    return float(np.sqrt(((xs - x) ** 2 + (ys - y) ** 2).min()))


def _bbox_dist(bbox: tuple[int, int, int, int], x: int, y: int) -> float:
    x0, y0, x1, y1 = bbox
    dx = max(x0 - x, 0, x - (x1 - 1))
    dy = max(y0 - y, 0, y - (y1 - 1))
    return float(np.hypot(dx, dy))


def _best_matching(
    cand: dict[int, list[tuple[float, int]]],
) -> dict[int, tuple[float, int]]:
    """Exact max-cardinality, min-total-distance seed->mask matching.

    ``cand``: seed_id -> [(distance, mask_index), ...] sorted.  Sizes are
    tiny (leftover seeds only); branch-and-bound with a node budget keeps
    the search exactly reproducible: prefer more matches, then lower total
    distance, then lexicographically smaller (seed, mask) assignment.
    Exceeding the budget raises -- a silently different (greedy) answer is
    exactly the mis-assignment failure mode this matcher exists to prevent.
    """
    seeds = sorted(cand, key=lambda s: (len(cand[s]), s))
    best: dict = {"key": None, "assign": {}}
    budget = [200_000]

    def rec(k: int, used: frozenset[int], assign: dict[int, tuple[float, int]],
            total: float, matched: int) -> None:
        if budget[0] <= 0:
            return
        budget[0] -= 1
        bound = matched + (len(seeds) - k)
        if best["key"] is not None:
            if -bound > best["key"][0]:
                return  # cannot reach the best cardinality any more
            if -bound == best["key"][0] and total >= best["key"][1]:
                return  # same cardinality at best, already costlier
        if k == len(seeds):
            key = (-matched, total, tuple(sorted(
                (s, m) for s, (_, m) in assign.items())))
            if best["key"] is None or key < best["key"]:
                best["key"] = key
                best["assign"] = dict(assign)
            return
        sid = seeds[k]
        for d, m in cand[sid]:
            if m in used:
                continue
            assign[sid] = (d, m)
            rec(k + 1, used | {m}, assign, total + d, matched + 1)
            del assign[sid]
        rec(k + 1, used, assign, total, matched)

    rec(0, frozenset(), {}, 0.0, 0)
    if budget[0] <= 0:
        raise RuntimeError(
            f"proximity matching search budget exhausted for {len(seeds)} "
            "seeds; refusing to return a possibly-suboptimal assignment"
        )
    return best["assign"]


def select_masks_by_seeds(
    raw: list[RawMask],
    seeds: list[Seed],
    image: np.ndarray,
    params: SelectParams | None = None,
) -> SelectionResult:
    """Claim masks for seeds.  See the module docstring for the phases."""
    p = params or SelectParams()
    if p.seed_kind not in ("render", "text"):
        raise ValueError(f"unknown seed_kind {p.seed_kind!r}")
    h, w = image.shape[:2]
    seeds = sorted(seeds, key=lambda s: s.seed_id)
    if len({s.seed_id for s in seeds}) != len(seeds):
        raise ValueError("duplicate seed ids")
    for s in seeds:
        if not (0 <= s.x < w and 0 <= s.y < h):
            raise ValueError(f"seed {s.seed_id} at ({s.x},{s.y}) outside image")

    result = SelectionResult({}, {}, [], [], {}, n_input_masks=len(raw))
    min_area = max(p.min_area_px, int(p.min_area_frac * h * w))
    max_area = int(p.max_area_frac * h * w)
    pool = [i for i, m in enumerate(raw) if min_area <= m.area <= max_area]
    result.n_pool = len(pool)
    if not pool or not seeds:
        result.unmatched = [(s.seed_id, "empty mask pool") for s in seeds]
        result.warnings.append("empty pool or no seeds")
        return result

    # ---- colour references --------------------------------------------------
    covered = np.zeros((h, w), dtype=bool)
    for i in pool:
        covered |= raw[i].mask
    uncovered = ~covered
    if uncovered.any():
        bg = np.median(image[uncovered], axis=0)
    else:
        bg = np.median(image.reshape(-1, 3), axis=0)
        result.warnings.append("no uncovered pixels; background colour is global median")
    # Land reference: disks around the seeds.  If seed surroundings resemble
    # the background colour, land is not colour-separable from water on this
    # map and all water-based exclusions are disabled (loudly).
    disk = np.zeros((h, w), dtype=bool)
    yy, xx = np.mgrid[-p.seed_disk_r:p.seed_disk_r + 1,
                      -p.seed_disk_r:p.seed_disk_r + 1]
    stamp = (yy ** 2 + xx ** 2) <= p.seed_disk_r ** 2
    for s in seeds:
        y0, y1 = max(s.y - p.seed_disk_r, 0), min(s.y + p.seed_disk_r + 1, h)
        x0, x1 = max(s.x - p.seed_disk_r, 0), min(s.x + p.seed_disk_r + 1, w)
        disk[y0:y1, x0:x1] |= stamp[
            (y0 - s.y + p.seed_disk_r):(y1 - s.y + p.seed_disk_r),
            (x0 - s.x + p.seed_disk_r):(x1 - s.x + p.seed_disk_r),
        ]
    ambiguous = _water_frac(image, disk, bg, p.water_px_dist)
    water_enabled = ambiguous < p.water_ambiguous_frac
    if not water_enabled:
        result.warnings.append(
            f"water exclusions disabled: {ambiguous:.0%} of seed-neighbourhood "
            "pixels resemble the background colour"
        )

    # ---- per-mask features --------------------------------------------------
    wf = {i: _water_frac(image, raw[i].mask, bg, p.water_px_dist) for i in pool}
    watery = {i for i in pool if water_enabled and wf[i] > p.water_frac_max}

    def is_pill(i: int) -> bool:
        x0, y0, x1, y1 = raw[i].bbox
        bw, bh = x1 - x0, y1 - y0
        return bh <= p.pill_max_height and bw >= p.pill_min_aspect * bh

    seed_pts = {s.seed_id: (s.x, s.y) for s in seeds}
    contains = {
        i: tuple(s.seed_id for s in seeds if raw[i].mask[s.y, s.x]) for i in pool
    }
    nz = {i: np.nonzero(raw[i].mask) for i in pool}
    cap = p.max_claim_dist
    if cap is None:
        cap = max(16.0, 0.013 * float(np.hypot(h, w)))

    claimed_px = np.zeros((h, w), dtype=bool)
    claimed_masks: set[int] = set()
    matched: set[int] = set()

    def claim(seed_id: int, mask_idx: int | None, pixels: np.ndarray,
              provenance: str, distance: float,
              shared_with: tuple[int, ...] = (),
              flags: tuple[str, ...] = ()) -> None:
        nonlocal claimed_px
        idx = (mask_idx,) if mask_idx is not None else ()
        result.claims[seed_id] = Claim(
            seed_id, idx, provenance, round(distance, 2), shared_with, flags)
        result.masks[seed_id] = pixels
        claimed_px = claimed_px | pixels
        if mask_idx is not None:
            claimed_masks.add(mask_idx)
        matched.add(seed_id)

    # ---- phase 1: contained -------------------------------------------------
    for s in seeds:
        cands = [i for i in pool
                 if contains[i] == (s.seed_id,) and i not in watery]
        if p.seed_kind == "text":
            cands = [i for i in cands if not is_pill(i)]
        cands.sort(key=lambda i: (is_pill(i), raw[i].area, raw[i].bbox, i))
        if cands:
            i = cands[0]
            flags = ("pill-shaped",) if is_pill(i) else ()
            claim(s.seed_id, i, raw[i].mask.copy(), "contained", 0.0, flags=flags)

    # ---- phases 2+3: remnant fixpoint interleaved with matched proximity ----
    remnant_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))

    def remnant_offer(s: Seed) -> tuple[np.ndarray, int] | None:
        # Claimed pixels are subtracted DILATED (2 px): a composite parent
        # includes the sub-stroke sliver web between its claimed children,
        # and without the dilation the remnant drags that web along --
        # producing pieces that reach other seeds' anchors (observed: the
        # Eastern-US piece of the North-America composite grabbed the
        # stroke sliver at Northwest Territory's anchor).
        claimed_grown = cv2.dilate(
            claimed_px.astype(np.uint8), remnant_kernel).astype(bool)
        parents = sorted(
            (i for i in pool if s.seed_id in contains[i] and i not in claimed_masks
             and not (p.seed_kind == "text" and is_pill(i))),
            key=lambda i: (raw[i].area, raw[i].bbox, i))
        for i in parents:
            piece_src = raw[i].mask & ~claimed_grown
            if not piece_src[s.y, s.x]:
                continue
            n, comp = cv2.connectedComponents(
                piece_src.astype(np.uint8), connectivity=4)
            piece = comp == comp[s.y, s.x]
            if int(piece.sum()) < min_area:
                continue
            if any(piece[y, x] for sid, (x, y) in seed_pts.items()
                   if sid != s.seed_id and sid not in matched):
                continue
            if water_enabled and _water_frac(
                    image, piece, bg, p.water_px_dist) > p.water_frac_max:
                continue
            return piece, i
        return None

    def proximity_candidates(s: Seed) -> list[tuple[float, int]]:
        out = []
        for i in pool:
            if (i in claimed_masks or i in watery or contains[i]
                    or is_pill(i)):
                continue
            if _bbox_dist(raw[i].bbox, s.x, s.y) > cap:
                continue
            d = 0.0 if raw[i].mask[s.y, s.x] else _mask_dist(nz[i], s.x, s.y)
            if d <= cap:
                out.append((d, i))
        out.sort(key=lambda t: (t[0], raw[t[1]].area, raw[t[1]].bbox, t[1]))
        return out[: p.proximity_max_candidates]

    for _ in range(4 * len(seeds) + 4):  # generous, provably enough: every
        progress = False                 # round either claims or breaks
        for s in seeds:                  # remnants to fixpoint first
            if s.seed_id in matched:
                continue
            offer = remnant_offer(s)
            if offer is not None:
                piece, parent = offer
                other = tuple(sid for sid in sorted(matched)
                              if piece[seed_pts[sid][1], seed_pts[sid][0]])
                flags = (("contains-matched-seed",) + tuple(map(str, other))
                         if other else ())
                claim(s.seed_id, parent, piece, "remnant", 0.0, flags=flags)
                progress = True
        if progress:
            continue
        cand = {s.seed_id: proximity_candidates(s)
                for s in seeds if s.seed_id not in matched}
        cand = {sid: c for sid, c in cand.items() if c}
        if not cand:
            break
        assign = _best_matching(cand)
        if not assign:
            break
        # apply only the single best edge, then re-enter the remnant fixpoint
        sid, (d, i) = min(assign.items(), key=lambda kv: (kv[1][0], kv[0]))
        claim(sid, i, raw[i].mask.copy(), "proximity", d)
    else:  # pragma: no cover - defensive; the loop always breaks or finishes
        result.warnings.append("phase loop hit its round limit")

    # ---- phase 4: unresolved merges ----------------------------------------
    for s in seeds:
        if s.seed_id in matched:
            continue
        unmatched_ids = {x.seed_id for x in seeds if x.seed_id not in matched}
        cands = [
            i for i in pool
            if s.seed_id in contains[i] and i not in watery
            and i not in claimed_masks
            and len(contains[i]) >= 2 and set(contains[i]) <= unmatched_ids
        ]
        cands.sort(key=lambda i: (raw[i].area, raw[i].bbox, i))
        if not cands:
            continue
        i = cands[0]
        group = contains[i]
        result.merges.append(group)
        result.warnings.append(
            f"UNRESOLVED MERGE: seeds {list(group)} share mask {i} "
            "with no separating mask; reported, not split"
        )
        for sid in group:
            claim(sid, i, raw[i].mask.copy(), "merged", 0.0,
                  shared_with=tuple(x for x in group if x != sid))

    # ---- unmatched reporting ------------------------------------------------
    for s in seeds:
        if s.seed_id in matched:
            continue
        best = min(
            ((0.0 if raw[i].mask[s.y, s.x] else _mask_dist(nz[i], s.x, s.y), i)
             for i in pool),
            default=(float("inf"), -1),
        )
        result.unmatched.append((
            s.seed_id,
            f"no containing, remnant, proximity (<= {cap:.0f}px) or merge "
            f"candidate; nearest pool mask {best[1]} at {best[0]:.1f}px",
        ))

    # ---- phase 5: archipelago attachment (many-to-one) ----------------------
    if p.attach_orphans and result.masks:
        group_of = {}
        for g in result.groups():
            for sid in g:
                group_of[sid] = g[0]
        for _ in range(len(pool)):
            changed = False
            for i in pool:
                if i in claimed_masks or contains[i] or i in watery or is_pill(i):
                    continue
                m = raw[i].mask
                cov = float((m & claimed_px).sum()) / max(raw[i].area, 1)
                if cov > p.dup_covered_frac:
                    claimed_masks.add(i)  # duplicate of claimed area; drop
                    continue
                med = np.median(image[m], axis=0)
                offers = []
                for g in result.groups():
                    tm = result.masks[g[0]]
                    ys, xs = np.nonzero(tm)
                    if not len(xs):
                        continue
                    x0, y0, x1, y1 = raw[i].bbox
                    if (xs.max() < x0 - p.attach_max_dist
                            or xs.min() > x1 + p.attach_max_dist
                            or ys.max() < y0 - p.attach_max_dist
                            or ys.min() > y1 + p.attach_max_dist):
                        continue
                    iy, ix = nz[i]
                    # distance between the two pixel sets, subsampled for cost
                    step = max(1, len(ix) // 400)
                    d = np.sqrt(
                        (ix[::step, None] - xs[None, ::4]) ** 2
                        + (iy[::step, None] - ys[None, ::4]) ** 2
                    ).min()
                    if d > p.attach_max_dist:
                        continue
                    tmed = np.median(image[tm], axis=0)
                    if np.linalg.norm(med - tmed) > p.attach_color_dist:
                        continue
                    offers.append((float(d), g[0]))
                if not offers:
                    continue
                offers.sort()
                d, sid = offers[0]
                result.masks[sid] = result.masks[sid] | m
                result.attached[sid] = result.attached.get(sid, ()) + (i,)
                claimed_masks.add(i)
                claimed_px = claimed_px | m
                changed = True
            if not changed:
                break

    result.n_rejected_no_seed = len(
        [i for i in pool if i not in claimed_masks])
    return result


def build_label_map(
    result: SelectionResult, image_shape: tuple[int, int]
) -> tuple[np.ndarray, list[tuple[int, ...]]]:
    """Paint claims into an int32 label map.

    One label per claim group (merged seeds share a label).  Groups paint in
    descending pixel-area order so smaller territories overwrite composites;
    interior holes (printed names) are filled.  Returns the map and the
    group list where ``groups[label - 1]`` are the seed ids of that label.
    """
    from riskdyn.segment.candidates import fill_holes

    h, w = image_shape
    label_map = np.zeros((h, w), dtype=np.int32)
    groups = result.groups()
    order = sorted(
        range(len(groups)),
        key=lambda k: (-int(result.masks[groups[k][0]].sum()), groups[k]),
    )
    for k in order:
        label_map[fill_holes(result.masks[groups[k][0]])] = k + 1
    return label_map, groups


def _main(argv: list[str] | None = None) -> int:
    """Run seed-driven selection on map 1 with the fixture anchors.

    Writes ``overlay.png`` and ``select_report.json`` under the map's
    processed directory and prints honest bijection numbers (buffer=0).
    """
    import argparse

    from riskdyn.segment import catalog as cat
    from riskdyn.segment.candidates import CandidateParams
    from riskdyn.segment.geometry import close_label_gaps, extract_territories
    from riskdyn.segment.ground_truth import load_label_points
    from riskdyn.segment.loader import load_map_image
    from riskdyn.segment.overlay import draw_overlay
    from riskdyn.segment.report import bijection_check
    from riskdyn.segment.sam import cached_generate

    ap = argparse.ArgumentParser(description="seed-driven mask selection")
    ap.add_argument("map_id", type=int, nargs="?", default=1)
    ap.add_argument("--no-artifacts", action="store_true")
    args = ap.parse_args(argv)
    if args.map_id != 1:
        raise SystemExit(
            "only map 1 has seeds today (fixture anchors); other maps get "
            "seeds from the text-detection stage when it lands"
        )

    summary = cat.load_catalog()[args.map_id]
    image = load_map_image(
        cat.image_path(args.map_id), (summary.width, summary.height))
    out_dir = cat.REPO_ROOT / "data" / "processed" / "maps" / str(args.map_id)
    raw = cached_generate(image, out_dir / "sam_masks.npz")
    points = load_label_points(
        cat.REPO_ROOT / "tests" / "fixtures" / "game_map1_territories.html")
    seeds = [Seed(pt.territory_id, pt.x, pt.y, pt.name) for pt in points]

    result = select_masks_by_seeds(raw, seeds, image)
    label_map, groups = build_label_map(result, image.shape[:2])
    cp = CandidateParams()
    label_map = close_label_gaps(
        label_map, image, cp.gap_close_px, cp.land_claim_px, cp.land_color_dist)
    shapes = extract_territories(label_map)
    bij = bijection_check(shapes, points)

    n_matched = len(result.claims)
    report = {
        "map_id": args.map_id,
        "n_seeds": len(seeds),
        "n_input_masks": result.n_input_masks,
        "n_pool": result.n_pool,
        "n_selected_groups": len(groups),
        "n_rejected_no_seed": result.n_rejected_no_seed,
        "n_matched_seeds": n_matched,
        "claims": {
            str(sid): {
                "provenance": c.provenance,
                "masks": list(c.mask_indices),
                "distance": c.distance,
                "shared_with": list(c.shared_with),
                "flags": list(c.flags),
                "attached": list(result.attached.get(sid, ())),
            }
            for sid, c in sorted(result.claims.items())
        },
        "unmatched": [
            {"seed_id": sid, "reason": reason} for sid, reason in result.unmatched
        ],
        "merges": [list(g) for g in result.merges],
        "warnings": result.warnings,
        "bijection_buffer0": bij,
    }
    header = (
        f"map {args.map_id} {summary.name}: seed-driven selection | "
        f"{len(shapes)} polygons / {summary.num_territories} expected | "
        f"bijection {bij['n_bijective']}/{bij['n_labels']} (buffer=0)"
    )
    print(header)
    for u in report["unmatched"]:
        print(f"  unmatched seed {u['seed_id']}: {u['reason']}")
    for warning in result.warnings:
        print(f"  warning: {warning}")
    if not args.no_artifacts:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "select_report.json").write_text(json.dumps(report, indent=1))
        draw_overlay(image, shapes, out_dir / "overlay.png", None, header)
        print(f"wrote {out_dir / 'overlay.png'} and select_report.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
