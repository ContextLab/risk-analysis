# Issue #4: colour-sampled region proposals (riskdyn/workbench/regions.py)

Session: 2026-08-11. Implements the segmentation-free region proposal path:
sample each territory's artwork colour at D12's own node coordinate, cluster
in Lab, report + overlay, optional `--write` into annotations.json.

## Key findings (verified against real artwork)

1. **D12's (x, y) is the TOP-LEFT of the ~30x20 `territory-large` label div**,
   not its center. `tests/fixtures/mappanel_map1.html` line 162 shows
   `style="left: 92px; top: 68px"` == `data-x/data-y`. Sampling at the raw
   point lands on coastline/ocean for ~1/3 of map 1's nodes (Alaska, Japan,
   Madagascar, Indonesia, PNG all sampled ocean blue). Sampling at the box
   center `(x+15, y+10)` fixes it: mean patch consistency across maps
   1/2/7/9/56/104 rose from 0.43-0.76 to 0.92-1.00. `LABEL_BOX_OFFSET` in
   regions.py.

2. **A plain largest-gap threshold rule fails on map 1.** Sorted single-linkage
   merge distances end `... 13.5 14.7 | 18.3 28.1 28.4 33.5 44.5`; the correct
   cut is at ~16.5 (6 continents) but the largest gap sits between the
   between-continent merges (33.5->44.5), yielding 2 clusters. Default
   threshold is instead the single-linkage cut maximizing mean silhouette
   (candidates = midpoints between consecutive sorted MST weights). This
   recovers map 1's six continents exactly, matching the authored
   territory_names ground truth.

3. Border-stroke sample point used by the test: raw node (594, 97) -> sample
   point (609, 107), exactly on the black Ukraine/Ural border stroke
   (verified visually); consistency fraction ~0.0 -> flagged unreliable.

4. Map 9 (uncatalogued, Saturn): 30 territories, 4 colour clusters. The
   artwork's red/purple/blue/brown wedges are genuinely similar muted tones;
   human review via region_overlay.png needed (as for all maps: this output
   is proposal-quality, source `colour-sample`, confidence low).

## Guarantees

- `--write` refuses (WriteRefused, CLI exit 2) if any existing region has
  source != "colour-sample" (map 1's vision-legend regions stay untouched)
  unless `--force`. Written region_ids are always one-element lists; written
  files still pass graph_build.validate_annotations.
- Nothing forces cluster count to catalog num_regions (map 7: catalog 12,
  colour groups found 7 -- city bonuses are legend-only overlaps).

## Artifacts

- `data/processed/maps/<id>/region_sample.json` + `region_overlay.png`
  (overlay is local-only per repo policy on D12 artwork).
- Tests: `tests/test_regions.py` (5 tests, real data only).
- Batch triage table: `./.venv/bin/python -m riskdyn.workbench.regions --all`.
