# Issue #4 stage 1 — map segmentation session notes (2026-08-10)

## Status

- `riskdyn/segment/` package built: loader, catalog, sam, candidates,
  geometry, overlay, report, pipeline, ground_truth.
- `tests/test_segment.py` added (bijection gate, PNG/alpha map 30,
  dimension assert, determinism, ocean exclusion).
- Deps added to pyproject: numpy, pillow, torch, torchvision (required by
  transformers SAM image processor), transformers, opencv-python-headless.
- First commit: 1288dbc.

## Key findings / gotchas

- **transformers v5**: the mask-generation pipeline parameter is
  `points_per_crop` (grid is n x n), NOT `points_per_side`. Unknown kwargs
  are silently dropped by `_sanitize_parameters`.
- **torchvision is load-bearing**: without it transformers falls back to a
  SAM image-processing path whose point grid is float64, which crashes on
  MPS ("Cannot convert a MPS Tensor to float64"). Keep torchvision
  installed and declared.
- Default SAM thresholds (0.88/0.95) on World Classic yield mostly
  whole-continent masks: 46 territories, bijection 11/42.
- Lowering to iou 0.7 / stability 0.85 yields n=91, bijection 21/42 —
  more junk (legend inset, "World Classic" title letters, text boxes)
  and still missing many territories.
- Pinned revisions: sam-vit-base 70c1a07f..., sam-vit-large 6851e044...,
  sam-vit-huge 87aecf0d...
- Map 1 SAM run (base, MPS): ~10-20 s.

## Current experiment

sweep3 (background): base p32/p48, large p32, huge p32 at iou .7 /
stab .85, on map 1, out_root under scratchpad/sweep3. Choose best, then
tune candidates.py (composite suppression, legend/text junk removal).

## To do

- Pick model+params; update SamParams defaults.
- Improve post-processing until bijection is honestly maximal.
- Run maps 1, 79, 100, 34; LOOK at each overlay; report per-map.
- Full 77-map run in background.
- Full test suite (121 existing + new) must pass.
- Write /tmp/riskdyn-segment-stage1.md report.
