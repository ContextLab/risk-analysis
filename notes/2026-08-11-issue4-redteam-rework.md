# Issue #4 Stage 1 — red-team rework session (2026-08-11)

Reworked the Stage-1 segmentation after an independent red team found two
Critical problems in the original implementation (whose session is gone).
Full detail in `/tmp/riskdyn-segment-stage1.md` (revised, corrections
marked). Summary for future sessions:

## What changed and why

1. **CRITICAL 1 (headline metric inflated):** the old 36/42 World Classic
   bijection was buffer-assisted (14 px `coastal_buffer_px` baked into the
   polygons); at buffer=0 it was 28/42. Now: `bijection` in report.json is
   computed on the emitted polygons with NO buffer (**33/42**, 37 in
   exactly one); `bijection_buffered` (**36/42**) is a clearly-labelled
   secondary for island labels D12 prints in open water. The buffer never
   touches emitted geometry any more (this also removes the 10-15 px
   coastal halo that would have manufactured false strait adjacencies in
   Stage 3).
2. **CRITICAL 2 (water junk):** absolute water-colour conviction — mask or
   painted label >60% pixels within RGB dist 35 of background is dropped
   regardless of isolation (`water_px_dist`/`water_frac_max` in
   `candidates.py`, `drop_water_labels` run before AND after gap closing).
   **Per-map guard** `water_ambiguous_frac=0.25`: on maps whose land
   resembles the background (Arctic Circle 0.37, Jungle 0.31) the rule is
   disabled with a warning — WITHOUT this guard it deletes Arctic Circle's
   grey half (reproduced, then fixed; verify overlay 79 if touching this).
3. **Coverage holes (the 6 mainland anchors):** `close_label_gaps` in
   `geometry.py` — ≤4 px stroke-gap claim (contested pixels reachable by 2
   labels within 4 px stay unclaimed; that keeps straits open and refuses
   to guess Mongolia's on-the-stroke anchor) + ≤12 px land claim for pixels
   ≥120 RGB from background (above the 45-90 coastal glow, so no halo).
4. **Corrections to the old report (both confirmed by zoomed crops):**
   - Madagascar: mainland coast really reaches x≈603 at row 449; anchor at
     (607,449) is 3.8 px off the MAINLAND, ~15 px from the island. Not
     "annexation of the island tip" (red team) and not merely a data quirk
     (original) — the old x=613 reach was untrimmed glow, now trimmed.
   - Irkutsk/Mongolia: border is a clearly drawn stroke; SAM+painting now
     yield clean separate polygons; only Mongolia's anchor (1.0 px from
     Irkutsk's mask, 1.4 px from Mongolia's) is unresolvable — left
     unclaimed (Irkutsk passes, Mongolia fails alone).
   - Greenland/Iceland: original claim verified CORRECT (Iceland's anchor
     is on Greenland's landmass) — kept.
5. **Tests:** exact pins replaced by floors/subsets/properties (see
   test_segment.py header comments). Suite 130 → **133 passed**, none
   weakened; determinism test still does two real SAM runs.

## Four-map verification (all overlays viewed after final state)

| map | found/expected | note |
|-|-|-|
| 1 | 85/42, b0 33/42, buffered 36/42 | was 103 polys, b0 28; junk (anchorless) 74→50; title letters/ocean chunks gone |
| 79 | 139/34 | guard kicked in; grey half intact |
| 100 | 283/150 | 9 water junk removed; territories intact |
| 34 | 497/60 | honest negative unchanged (foliage) |

## Open items / cautions

- Maps 2, 5, 6, 7 outputs on disk are from the OLD pipeline until rerun;
  the other 73 maps not rerun this session.
- 0.25 separability threshold calibrated on 4 maps only; per-map warning
  makes misclassification visible.
- b0 residual failures (9) are label-placement realities, characterized in
  tests/test_segment.py KNOWN_UNMATCHED_B0; a NEW failing id fails the
  suite loudly.
- Do NOT truncate to catalog num_territories (red team: top-42-by-area
  keeps only 32/39 real polygons); it stays a warning signal.
