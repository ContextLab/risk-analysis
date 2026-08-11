"""Tests for seed-driven mask selection (riskdyn.segment.select).

Two layers, both real (no mocks):

- synthetic geometry built from actual RawMask/ndarray objects exercising
  each documented phase (contained, remnant, proximity, merge, attach) and
  the failure reporting;
- the real World Classic artwork with the real cached SAM masks and the 42
  fixture anchors, asserting the honestly-measured floors and cross-process
  determinism.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import pathlib
import random
import subprocess
import sys

import numpy as np
import pytest

from riskdyn.segment.sam import RawMask
from riskdyn.segment.select import (
    _REF_DIAG,
    Seed,
    SelectParams,
    SelectionResult,
    _best_matching,
    build_label_map,
    select_masks_by_seeds,
)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "game_map1_territories.html"

BG = (20, 60, 90)          # water
LAND_A = (200, 180, 60)    # yellow
LAND_B = (90, 160, 70)     # green


def _mask(h, w, boxes):
    m = np.zeros((h, w), dtype=bool)
    for x0, y0, x1, y1 in boxes:
        m[y0:y1, x0:x1] = True
    return m


def _raw(mask, score=0.9):
    ys, xs = np.nonzero(mask)
    bbox = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    return RawMask(mask=mask, score=score, area=int(mask.sum()), bbox=bbox)


def _scene():
    """A 240x240 synthetic map exercising every phase.

    Layout (all on water-coloured background):
      A (20,20)-(70,70)    own mask + a text pill inside + a composite
      B (90,20)-(140,70)   NO own mask, only inside the composite -> remnant
      C (20,100)-(70,150)  shares one mask with D (no separating mask)
      D (80,100)-(130,150) shares that mask with C
      E (170,20)-(220,70)  own mask, but seed sits 3 px outside -> proximity
      F seed at (200,200)  no mask anywhere near -> unmatched
      junk (170,120)-(220,170)  mask containing no seed -> rejected
    """
    h = w = 240
    image = np.zeros((h, w, 3), dtype=np.uint8)
    image[:, :] = BG
    a = _mask(h, w, [(20, 20, 70, 70)])
    b = _mask(h, w, [(90, 20, 140, 70)])
    cd = _mask(h, w, [(20, 100, 130, 150)])
    e = _mask(h, w, [(170, 20, 220, 70)])
    junk = _mask(h, w, [(170, 120, 220, 170)])
    comp = a | b
    pill = _mask(h, w, [(30, 40, 62, 48)])  # 32x8 text pill inside A
    for m, color in ((comp, LAND_A), (cd, LAND_A), (e, LAND_B), (junk, LAND_B)):
        image[m] = color
    masks = [_raw(comp), _raw(cd), _raw(a), _raw(e), _raw(junk), _raw(pill)]
    seeds = [
        Seed(1, 40, 44, "A"),     # inside a, pill, comp
        Seed(2, 115, 45, "B"),    # inside b and comp only
        Seed(3, 45, 125, "C"),    # inside cd only
        Seed(4, 105, 125, "D"),   # inside cd only
        Seed(5, 167, 45, "E"),    # 3 px left of e
        Seed(6, 200, 200, "F"),   # nowhere
    ]
    return image, masks, seeds


def _params(**kw):
    defaults = dict(min_area_px=64, min_area_frac=0.0, attach_orphans=False)
    defaults.update(kw)
    return SelectParams(**defaults)


# ------------------------------------------------------------ synthetic

def test_contained_prefers_tightest_non_pill():
    image, masks, seeds = _scene()
    res = select_masks_by_seeds(masks, seeds, image, _params())
    c = res.claims[1]
    assert c.provenance == "contained"
    # claims A's own mask: not the pill (tighter), not the composite (looser)
    assert masks[c.mask_indices[0]].area == 50 * 50
    assert "pill-shaped" not in c.flags


def test_pill_claimed_only_as_last_resort_for_render_seeds():
    image, masks, seeds = _scene()
    pill_only = [m for m in masks if m.area == 32 * 8]
    res = select_masks_by_seeds(pill_only, [seeds[0]], image, _params())
    assert res.claims[1].provenance == "contained"
    assert "pill-shaped" in res.claims[1].flags


def test_text_seeds_never_claim_pills_by_containment():
    image, masks, seeds = _scene()
    pill_only = [m for m in masks if m.area == 32 * 8]
    res = select_masks_by_seeds(
        pill_only, [seeds[0]], image, _params(seed_kind="text"))
    assert 1 not in res.claims
    assert res.unmatched and res.unmatched[0][0] == 1


def test_remnant_carves_composite_minus_claimed():
    image, masks, seeds = _scene()
    res = select_masks_by_seeds(masks, seeds, image, _params())
    c = res.claims[2]
    assert c.provenance == "remnant"
    piece = res.masks[2]
    assert piece[45, 115]                 # contains B's seed
    assert not piece[44, 40]              # not A's region
    assert not (piece & res.masks[1]).any()


def test_shared_mask_reported_as_unresolved_merge():
    image, masks, seeds = _scene()
    res = select_masks_by_seeds(masks, seeds, image, _params())
    assert (3, 4) in res.merges
    assert res.claims[3].provenance == "merged"
    assert res.claims[3].shared_with == (4,)
    assert res.claims[4].shared_with == (3,)
    assert any("UNRESOLVED MERGE" in wtext for wtext in res.warnings)


def test_seed_outside_all_masks_claims_by_proximity():
    image, masks, seeds = _scene()
    res = select_masks_by_seeds(masks, seeds, image, _params())
    c = res.claims[5]
    assert c.provenance == "proximity"
    assert c.distance == pytest.approx(3.0, abs=0.01)
    assert masks[c.mask_indices[0]].mask[45, 200]


def test_far_seed_is_reported_unmatched_not_dropped():
    image, masks, seeds = _scene()
    res = select_masks_by_seeds(masks, seeds, image, _params())
    assert [sid for sid, _ in res.unmatched] == [6]
    assert "nearest pool mask" in res.unmatched[0][1]


def test_anchorless_masks_rejected_by_construction():
    image, masks, seeds = _scene()
    res = select_masks_by_seeds(masks, seeds, image, _params())
    claimed = {i for c in res.claims.values() for i in c.mask_indices}
    junk_idx = next(i for i, m in enumerate(masks)
                    if m.mask[125, 200] and m.area == 50 * 50)
    assert junk_idx not in claimed
    assert res.n_rejected_no_seed >= 1
    label_map, groups = build_label_map(res, image.shape[:2])
    assert int(label_map.max()) == len(groups)
    for g in groups:            # every painted label traces back to seeds
        assert g and all(sid in res.claims for sid in g)
    assert not label_map[125:170, 170:220].any()  # junk mask not painted


def test_proximity_matching_is_optimal_not_greedy():
    """The measured Britain/Iceland trap: seed 1 is NEARER to the only
    mask seed 2 can reach than to its own.  Greedy nearest-first strands
    seed 2; max-cardinality matching resolves both.  The cap is explicit:
    this tests the matcher, not the scale-relative default cap (which is
    ~2 px on a 120 px image)."""
    h = w = 120
    image = np.zeros((h, w, 3), dtype=np.uint8)
    image[:, :] = BG
    mx = _mask(h, w, [(40, 40, 80, 75)])   # seed 2's ONLY option
    my = _mask(h, w, [(20, 15, 47, 45)])   # seed 1's own mask, farther
    image[mx] = LAND_A
    image[my] = LAND_B
    masks = [_raw(mx), _raw(my)]
    s1 = Seed(1, 60, 32, "britain-like")   # 8 px above mx, 14 px right of my
    s2 = Seed(2, 60, 86, "iceland-like")   # 12 px below mx, 44 px from my
    res = select_masks_by_seeds(masks, [s1, s2], image,
                                _params(max_claim_dist=20.0))
    assert res.unmatched == []
    assert res.claims[2].mask_indices == (0,)   # s2 got its only option
    assert res.claims[1].mask_indices == (1,)   # s1 pushed to its own mask
    assert res.claims[1].distance == pytest.approx(14.0, abs=0.01)
    assert res.claims[2].distance == pytest.approx(12.0, abs=0.01)


def test_validation_errors():
    image, masks, seeds = _scene()
    with pytest.raises(ValueError, match="duplicate seed ids"):
        select_masks_by_seeds(masks, [seeds[0], seeds[0]], image, _params())
    with pytest.raises(ValueError, match="outside image"):
        select_masks_by_seeds(masks, [Seed(9, 5000, 5, "x")], image, _params())
    with pytest.raises(ValueError, match="seed_kind"):
        select_masks_by_seeds(masks, seeds, image, _params(seed_kind="ocr"))


def test_synthetic_determinism():
    image, masks, seeds = _scene()
    a = select_masks_by_seeds(masks, seeds, image, _params())
    b = select_masks_by_seeds(masks, seeds, image, _params())
    assert a.claims == b.claims
    assert a.unmatched == b.unmatched and a.merges == b.merges
    for sid in a.masks:
        assert np.array_equal(a.masks[sid], b.masks[sid])
    la, ga = build_label_map(a, image.shape[:2])
    lb, gb = build_label_map(b, image.shape[:2])
    assert ga == gb and np.array_equal(la, lb)


def _cross_scene():
    """A 300x300 cross: centre X has its own mask (contained claim) and
    THREE arm seeds live only inside the one composite = X | arms."""
    h = w = 300
    image = np.zeros((h, w, 3), dtype=np.uint8)
    image[:, :] = BG
    x_blk = _mask(h, w, [(130, 130, 170, 170)])
    comp = x_blk | _mask(h, w, [(130, 40, 170, 130), (40, 130, 130, 170),
                                (170, 130, 260, 170)])
    image[comp] = LAND_A
    masks = [_raw(comp), _raw(x_blk)]
    seeds = [Seed(1, 150, 150, "X"), Seed(2, 150, 80, "N"),
             Seed(3, 80, 150, "W"), Seed(4, 220, 150, "E")]
    return image, masks, seeds


def test_every_sibling_in_a_composite_can_carve_a_remnant():
    """A remnant claim consumes only its piece, never the whole parent:
    all three arm seeds of the cross composite must carve their own
    remnant.  Before the fix the first carve marked the parent claimed
    and stranded the other two arms as unmatched."""
    image, masks, seeds = _cross_scene()
    res = select_masks_by_seeds(masks, seeds, image, _params())
    assert res.unmatched == []
    assert res.claims[1].provenance == "contained"
    for sid in (2, 3, 4):
        assert res.claims[sid].provenance == "remnant"
    # the carved-up parent is used, not "rejected"
    assert res.n_rejected_no_seed == 0
    # pieces are pairwise disjoint and each contains its own seed only
    pts = {s.seed_id: (s.x, s.y) for s in seeds}
    for a, b in itertools.combinations(sorted(res.masks), 2):
        assert not (res.masks[a] & res.masks[b]).any()
    for sid, (x, y) in pts.items():
        assert res.masks[sid][y, x]
        for other, (ox, oy) in pts.items():
            if other != sid:
                assert not res.masks[sid][oy, ox]


def test_best_matching_tiebreak_is_lexicographic_and_order_independent():
    """Two seeds, two masks, all four edges distance 5: both perfect
    matchings tie on cardinality and total.  The winner must be the
    assignment whose sorted (seed, mask-key) tuple is smallest, no matter
    how the candidate dict or its lists are ordered.  Before the fix the
    first equal-total leaf found won, so reversing the candidate lists
    flipped the assignment."""
    key = {10: (100, (0, 0, 10, 10), 10), 20: (100, (50, 0, 60, 10), 20)}
    expected = {1: (5.0, 10), 2: (5.0, 20)}   # mask 10 has the smaller key
    base = [(5.0, 10), (5.0, 20)]
    for l1 in itertools.permutations(base):
        for l2 in itertools.permutations(base):
            for order in itertools.permutations([1, 2]):
                lists = {1: list(l1), 2: list(l2)}
                cand = {s: lists[s] for s in order}
                assert _best_matching(cand, key) == expected


def _semantic(res: SelectionResult) -> tuple:
    """Order-free summary of a selection: per-seed provenance, distance
    and claimed pixels; unmatched seed ids; merge groups.  Mask INDICES
    are deliberately excluded (they live in input-list order)."""
    return (
        {sid: (c.provenance, c.distance, c.shared_with,
               res.masks[sid].tobytes())
         for sid, c in res.claims.items()},
        sorted(sid for sid, _ in res.unmatched),
        sorted(tuple(sorted(g)) for g in res.merges),
    )


def test_selection_invariant_under_seed_and_mask_permutation():
    """Determinism under input permutation: shuffling the mask list and
    the seed list must not change which pixels any seed ends up with, in
    either the phase scene or a distance-tie scene."""
    scenes = []
    scenes.append(_scene() + (_params(),))

    # symmetric tie: both seeds equidistant (21 px) from two equal-sized
    # masks; both perfect matchings have the same total distance
    h = w = 200
    image = np.zeros((h, w, 3), dtype=np.uint8)
    image[:, :] = BG
    ma = _mask(h, w, [(40, 40, 80, 80)])
    mb = _mask(h, w, [(121, 40, 161, 80)])
    image[ma] = LAND_A
    image[mb] = LAND_B
    scenes.append((image, [_raw(ma), _raw(mb)],
                   [Seed(1, 100, 60, "mid"), Seed(2, 100, 100, "low")],
                   _params(max_claim_dist=40.0)))

    rng = random.Random(0)
    for image, masks, seeds, params in scenes:
        ref = _semantic(select_masks_by_seeds(masks, seeds, image, params))
        for trial in range(4):
            pm = list(masks)
            ps = list(seeds)
            if trial == 0:
                pm.reverse()
                ps.reverse()
            else:
                rng.shuffle(pm)
                rng.shuffle(ps)
            got = _semantic(select_masks_by_seeds(pm, ps, image, params))
            assert got == ref


def _scaled_scene(s: int):
    """The same map at s-times resolution: a territory with a text pill
    inside it (pill is 30*s px tall: a pill at every scale only if the
    pill test is scale-relative) plus a proximity seed 10*s px from its
    mask."""
    h, w = 700 * s, 1000 * s
    image = np.zeros((h, w, 3), dtype=np.uint8)
    image[:, :] = BG
    terr = _mask(h, w, [(100 * s, 100 * s, 250 * s, 250 * s)])
    pill = _mask(h, w, [(120 * s, 150 * s, 200 * s, 180 * s)])
    island = _mask(h, w, [(600 * s, 300 * s, 700 * s, 380 * s)])
    image[terr] = LAND_A
    image[island] = LAND_B
    masks = [_raw(terr), _raw(pill), _raw(island)]
    seeds = [Seed(1, 160 * s, 165 * s, "T"), Seed(2, 590 * s, 340 * s, "P")]
    return image, masks, seeds


@pytest.mark.parametrize("s", [1, 2])
def test_scale_relative_defaults_1x_2x(s):
    """Doubling the resolution must not change behaviour: the 60 px pill
    at 2x is still a pill (with the old absolute pill_max_height=40 it was
    claimed as the territory), the proximity distance doubles with the
    cap, and areas scale by s**2."""
    image, masks, seeds = _scaled_scene(s)
    res = select_masks_by_seeds(masks, seeds, image,
                                SelectParams(seed_kind="text"))
    assert res.unmatched == []
    assert res.claims[1].provenance == "contained"
    assert int(res.masks[1].sum()) == (150 * 150) * s * s   # territory, NOT pill
    assert res.claims[2].provenance == "proximity"
    assert res.claims[2].distance == pytest.approx(10.0 * s, abs=0.01)


# ------------------------------------------------------------ real map 1

@pytest.fixture(scope="module")
def map1_selection():
    from riskdyn.segment import catalog as cat
    from riskdyn.segment.ground_truth import load_label_points
    from riskdyn.segment.loader import load_map_image
    from riskdyn.segment.sam import cached_generate

    summary = cat.load_catalog()[1]
    image = load_map_image(cat.image_path(1), (summary.width, summary.height))
    raw = cached_generate(
        image, cat.REPO_ROOT / "data" / "processed" / "maps" / "1" / "sam_masks.npz")
    points = load_label_points(FIXTURE)
    seeds = [Seed(p.territory_id, p.x, p.y, p.name) for p in points]
    result = select_masks_by_seeds(raw, seeds, image)
    return image, raw, points, seeds, result


def test_map1_every_seed_resolved(map1_selection):
    """Floor: on World Classic every one of the 42 seeds gets a claim
    (39 exclusive + 2 sharing the Greenland/Iceland merge + Eastern US
    style remnants) and none is silently dropped."""
    _, _, _, seeds, res = map1_selection
    assert len(res.claims) == 42
    assert res.unmatched == []
    assert set(res.claims) == {s.seed_id for s in seeds}


def test_map1_greenland_iceland_is_a_reported_merge(map1_selection):
    """D12 prints Iceland's anchor on Greenland's landmass (verified on
    the artwork 2026-08-11); no SAM mask separates the two anchors, and
    the island mask is text-pill-shaped so it may not be claimed.  The
    honest outcome is a reported merge, not an invented split."""
    _, _, _, _, res = map1_selection
    assert (11, 32) in res.merges
    assert res.claims[11].shared_with == (32,)


def test_map1_no_anchorless_output(map1_selection):
    """The point of the exercise: every emitted label traces to a seed."""
    image, _, _, _, res = map1_selection
    label_map, groups = build_label_map(res, image.shape[:2])
    assert int(label_map.max()) == len(groups)
    assert all(g for g in groups)
    # 39-41 groups for 42 seeds (one merge pair); never the 85 of the
    # anchor-free pipeline, and never fewer than the seeds can justify
    assert 39 <= len(groups) <= 42


def test_map1_bijection_floors(map1_selection):
    """Floors STRICTLY BELOW the values measured 2026-08-11 with the
    emission the pipeline uses (close_label_gaps, gap 4 px, land tier
    12 px): measured polygon-level bijection 33/42 (floor 32), label-level
    containment 37/42 (floor 36), exclusive 35/42 (floor 34).  Floors sit
    one below the measurement so a genuine improvement can restructure
    results without tripping them, while any real regression (2+ seeds)
    still fails.  The characterized-failure subset is a regression guard,
    not a pin: fixing any member keeps the subset relation true; breaking
    a NEW territory violates it."""
    from riskdyn.segment.candidates import CandidateParams
    from riskdyn.segment.geometry import close_label_gaps, extract_territories
    from riskdyn.segment.report import bijection_check

    image, _, points, _, res = map1_selection
    label_map, groups = build_label_map(res, image.shape[:2])
    cp = CandidateParams()
    label_map = close_label_gaps(
        label_map, image, cp.gap_close_px, cp.land_claim_px, cp.land_color_dist)
    shapes = extract_territories(label_map)
    bij = bijection_check(shapes, points)
    assert bij["n_labels"] == 42
    assert bij["n_bijective"] >= 32, bij["failures"]
    failed = {f["territory_id"] for f in bij["failures"]}
    # characterized misses only (anchors on the wrong landmass, in open
    # water beyond stroke reach, on a contested border stroke, or on the
    # smaller piece of a multi-polygon territory)
    assert failed <= {11, 32, 41, 42, 43, 44, 56, 61, 66}, bij["failures"]

    own = {}
    for k, g in enumerate(groups, start=1):
        for sid in g:
            own[sid] = k
    in_own_label = sum(
        1 for p in points if label_map[p.y, p.x] == own[p.territory_id]
    )
    exclusive = sum(
        1 for p in points
        if label_map[p.y, p.x] == own[p.territory_id]
        and len(groups[own[p.territory_id] - 1]) == 1
    )
    assert in_own_label >= 36
    assert exclusive >= 34


def test_map1_claim_distances_are_bounded_and_explained(map1_selection):
    """Properties, not constants: every claim's distance respects the
    CONFIGURED cap (the scale-relative default, computed here the same
    way select does), and no seed is silently dropped -- each one is
    either claimed or listed in ``unmatched``, never both, never neither."""
    image, raw, _, seeds, res = map1_selection
    cap = 16.0 * float(np.hypot(*image.shape[:2])) / _REF_DIAG
    for sid, c in sorted(res.claims.items()):
        assert c.provenance in ("contained", "remnant", "proximity", "merged")
        assert c.distance <= cap + 1e-6
        if c.provenance in ("contained", "remnant", "merged"):
            assert c.distance == 0.0
    claimed = set(res.claims)
    unmatched = {sid for sid, _ in res.unmatched}
    assert claimed & unmatched == set()
    assert claimed | unmatched == {s.seed_id for s in seeds}


def test_map1_selection_deterministic_across_processes(map1_selection):
    """Same digest from a fresh interpreter: no set/dict iteration order
    leaks into the result (the cached SAM masks make this cheap)."""
    image, _, _, _, res = map1_selection

    def digest(result: SelectionResult, shape) -> str:
        label_map, groups = build_label_map(result, shape)
        payload = json.dumps(
            {
                "claims": {
                    str(sid): [c.mask_indices, c.provenance, c.distance,
                               c.shared_with, c.flags]
                    for sid, c in sorted(result.claims.items())
                },
                "merges": result.merges,
                "unmatched": result.unmatched,
                "groups": groups,
            },
            sort_keys=True, default=list,
        ).encode()
        return hashlib.sha256(payload + label_map.tobytes()).hexdigest()

    here = digest(res, image.shape[:2])
    script = (
        "import hashlib, json\n"
        "from riskdyn.segment import catalog as cat\n"
        "from riskdyn.segment.ground_truth import load_label_points\n"
        "from riskdyn.segment.loader import load_map_image\n"
        "from riskdyn.segment.sam import cached_generate\n"
        "from riskdyn.segment.select import Seed, build_label_map, select_masks_by_seeds\n"
        "summary = cat.load_catalog()[1]\n"
        "image = load_map_image(cat.image_path(1), (summary.width, summary.height))\n"
        "raw = cached_generate(image, cat.REPO_ROOT / 'data/processed/maps/1/sam_masks.npz')\n"
        "points = load_label_points(cat.REPO_ROOT / 'tests/fixtures/game_map1_territories.html')\n"
        "seeds = [Seed(p.territory_id, p.x, p.y, p.name) for p in points]\n"
        "res = select_masks_by_seeds(raw, seeds, image)\n"
        "label_map, groups = build_label_map(res, image.shape[:2])\n"
        "payload = json.dumps({'claims': {str(s): [c.mask_indices, c.provenance,"
        " c.distance, c.shared_with, c.flags] for s, c in sorted(res.claims.items())},"
        " 'merges': res.merges, 'unmatched': res.unmatched, 'groups': groups},"
        " sort_keys=True, default=list).encode()\n"
        "print(hashlib.sha256(payload + label_map.tobytes()).hexdigest())\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=600,
        cwd=str(pathlib.Path(__file__).resolve().parents[1]),
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().splitlines()[-1] == here
