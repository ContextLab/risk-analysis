# Issue #4: seed-driven mask selection (select.py) — session notes

Date: 2026-08-11. Scope: NEW `riskdyn/segment/select.py` + `tests/test_segment_select.py`
(parallel-team boundary: text.py owned by another agent; pipeline.py NOT modified —
select.py has its own `python -m riskdyn.segment.select 1` runner).

## What was built

`select_masks_by_seeds(raw_masks, seeds, image, params)` — claims SAM masks for
territory seed points. Seeds are always passed in (D12 render anchors for map 1
from the fixture; text-label centroids for all maps once the detector lands,
via `SelectParams(seed_kind="text")`). Phases: contained (tightest single-seed
mask, text-pill deprioritized/excluded) -> remnant carving (composite minus
DILATED claimed pixels, fixpoint) -> proximity (max-cardinality min-total-distance
exact matching, one edge per round, cap max(16, 0.013*diag)) -> unresolved-merge
reporting -> optional archipelago attachment (colour-consistent orphans).
`build_label_map()` paints claims (merged seeds share one label). Everything
deterministic; cross-process determinism tested via subprocess digest.

## Measurement that drove the design (coordinator directive, done first)

- 36/42 anchors inside SEVERAL pool masks (continents at d=0), 5 in none,
  1 only in a water strip: neither containment nor bare proximity discriminates.
- After structural exclusions + tightest tie-break, correct mask nearest for
  34/38 anchors that have one. Displacements up to 13.6 px (Britain — its anchor
  sits at the tail of the "Iceland" OCEAN TEXT; verified via crops).
- Iceland's anchor is ON Greenland's landmass (12.5 px from the Iceland island
  mask i174, which is text-pill-SHAPED 83x38 so it stays excluded -> honest merge).
- Britain nearer to Iceland's island (8.1) than to Britain's mask i155 (13.6):
  greedy matching provably wrong -> exact matching implemented.
- No own mask exists for Eastern US (remnant of NA composite i13/i8 only).
  Verified real masks: Mongolia=i84, N.Europe=i135, Alaska=i136 (partial),
  Scandinavia=i110, W.Aus=i76, PNG=i142, Madagascar=i140, Indonesia={i274,i83,...}.

## Honest numbers (map 1, buffer=0, emission = close_label_gaps 4/12 like pipeline)

- polygons 41 (was 85); anchorless polygons 0 by construction (was 50)
- polygon-level bijection 33/42 == baseline; failures {11,32,41,42,43,44,56,61,66}
  (baseline set was {11,32,41,42,43,56,61,63,66}: FIXED 63 W.Australia + 5/30/46
  which the polygon metric already credited; REGRESSED 44 Scandinavia — its anchor
  is 10.6 px off-mask on coastal text; the old pipeline's halo-mask happened to cover it)
- label-map-level containment 35/42; selection-level: 42/42 seeds claimed
  (40 exclusive + Greenland/Iceland reported merge)
- ≥38/42 target: VOIDED by coordinator after adversarial review; measured ceiling
  with faithful geometry at buffer=0 is ~35 (anchors physically in open water/wrong landmass)

## Files/commands

- `./.venv/bin/python -m riskdyn.segment.select 1` — writes overlay.png +
  select_report.json under data/processed/maps/1/ (UNTRACKED; note: running the
  main pipeline/tests regenerates overlay.png in the old 85-polygon style)
- tests: `./.venv/bin/python -m pytest tests/test_segment_select.py -q` (17 tests)
- measurement scripts in session scratchpad (ephemeral); full report /tmp/riskdyn-select.md

## Open items for the team

- text.py agent: use `Seed(...)` + `seed_kind="text"`; the 16 px proximity cap and
  pill handling are validated ONLY against render anchors on map 1.
- Multi-polygon territories: label map carries them, but geometry.extract_territories
  emits one polygon (largest component) per label — Indonesia's anchor is on a
  smaller island, so polygon-level bijection loses it. Needs a geometry.py decision.
- Iceland island mask is pill-shaped; a shape rule that frees it without letting
  text masks through was tried (bbox fill) and does NOT separate (text glyph fills
  go down to 0.38 vs island 0.21). OCR/text detection is the real fix.
