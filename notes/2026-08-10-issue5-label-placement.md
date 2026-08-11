# Session notes — 2026-08-10: issue #5, label placement

## What shipped

Collision-avoiding label placement in `riskdyn/maps/render.py`, replacing the fixed
`xytext=(0, 12)` offset. Greedy deterministic candidate search per label:

- Candidate fans around each node: 8 radii (12–64 pt) x 12 angles, then an extended tier
  (44–100 pt x 24 angles) for dense maps. Fixed ordering everywhere → deterministic.
- Three-tier degradation: (1) first candidate touching nothing (no label, node marker, or
  edge); (2) zero-overlap candidate crossed by fewest edges; (3) minimum soft score.
  Labels are never dropped.
- Labels displaced ≥26 pt get a thin leader line back to their node. Leader lines are
  treated as obstacles for subsequently placed labels, and a candidate whose own leader
  would cross a placed label is penalised.
- Determinism hardening: `svg.hashsalt = "riskdyn"` at module import; SVG `Date` / PDF
  `CreationDate` metadata stripped in `render_map` so re-renders are byte-identical
  across processes.
- Node positions never move (real geography); only labels.

`render_map` public signature unchanged. New internal `_build_figure()` returns
(fig, label_artists) so tests can measure real rendered text extents.

## Tests

`tests/test_render_labels.py` (5 new; suite 115 → 120, all passing):

- `test_world_classic_labels_do_not_overlap` — pairwise rendered text extents disjoint
  on the real 42-territory World Classic fixture.
- `test_world_classic_every_territory_has_exactly_one_label`
- `test_svg_render_is_deterministic_across_runs` / `..._across_processes` (subprocess).
- `test_dense_synthetic_map_no_overlaps_within_time_bound` — 150-territory staggered
  grid, no overlaps, placement well under the 30 s bound (~1 s total render).

Gotcha worth remembering: `Annotation.get_window_extent` unions in the arrow patch of
`arrowprops`, so overlap tests must measure `Text.get_window_extent` instead.

## Verified visually

World Classic before/after PNGs compared by eye: Western/Eastern Australia now separated;
Britain / Northern / Western / Southern Europe stack legibly on the left with leader
lines; Ontario no longer has an edge through its label; nothing regressed elsewhere.
Dense 150-territory render inspected: busy but every label present, non-overlapping,
leader-lined.

**Honest limits:** dense-map behaviour verified on a synthetic staggered grid only —
we have no real D12 topology above 42 territories (site access pending, see
d12-permission-request.md). Leader lines share styling family with edges (thinner,
0.5 vs 0.8, darker grey); on very dense maps a long leader can still pass close to
unrelated nodes.
