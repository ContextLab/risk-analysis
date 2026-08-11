# 2026-08-11 — select.py defect fixes (issue #4, adversarial review)

Agent scope: `riskdyn/segment/select.py` + `tests/test_segment_select.py`
only (a parallel agent wired the pipeline / multi-polygon emission).
Commit: `9780366`. Full report: `/tmp/riskdyn-select-fix.md`.

## What changed

1. **Remnant sibling stranding (CRITICAL)**: remnant claims no longer add
   the parent composite to `claimed_masks`; a new `remnant_parents` set
   keeps the parent carvable for every seed inside it while
   `n_rejected_no_seed` still counts it as used. Repro (cross composite,
   3 arm seeds): was 1 claim + 2 stranded, now all remnants.
2. **Matcher tie-break (CRITICAL)**: `_best_matching` prune changed
   `total >= best` -> strict `>`, and the tie-break key is now
   `sorted((seed, (area, bbox, index)))` — order-independent and genuinely
   lexicographic. Docstring states the matching is exact only over each
   seed's `proximity_max_candidates` nearest masks.
3. **Scale-relative pixel defaults (IMPORTANT)**: `seed_disk_r`,
   `pill_max_height`, `attach_max_dist`, claim cap default to map-1
   measured values scaled by `diag / _REF_DIAG` (`hypot(1021,689)`).
   `water_px_dist` is a COLOUR distance despite the name — documented, not
   scaled. Map 1 output bit-identical (digest test unchanged).
4. **Tests constrain, not pin (IMPORTANT)**: floors strictly below
   measured (32/36/34 vs measured 33/37/35), distance checked against the
   configured cap, claims+unmatched must partition the seed set. +5 tests
   (siblings, matcher permutations, API permutation invariance, 1x/2x
   scale x2).

## Measurements (map 1, my own script, not report.json)

- Anchorless at artifact level: **16/51 polygon pieces, 6/41 territories**
  (indices 3, 6, 11, 19, 33, 37). "0 by construction" was definitional
  spin; module docstring reworded.
- Size-changed territories (NW Territory 11436 px/2 pieces, Japan 1917/2,
  Siam 4615, East Africa 9980): each contains exactly its own anchor;
  crops visually match drawn borders -> improvements from multi-polygon
  emission + remnant carving. Old-path areas could not be re-derived
  (artifacts untracked).

## Open items

- Text-seed cap (16 px scaled) still unvalidated — waiting on text.py.
- Tie-break falls back to input index only for masks identical in BOTH
  area and bbox; no such pair known in real pools, not scanned for.
- Suite 155 passed after my commit `9780366`; coordinator re-ran the full
  suite with the parallel agent's pipeline commits (`4baebb7`, `6577587`,
  `f15a743`) in place: **160 passed**, zero failures.
- Follow-up commit: `_main` no longer writes `overlay.png` (the pipeline is
  the single intended writer; a hand-run once overwrote the pipeline's
  overlay and shipped a contradictory artifact). The debug harness now
  writes `select_overlay.png`; smoke-ran `--no-artifacts`: 41 polygons,
  bijection 33/42, unchanged.
