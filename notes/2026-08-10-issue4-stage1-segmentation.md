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

## Model/param sweep outcome (map 1 bijection out of 42)

- base p32 defaults: 11; base p32 loose: 21; base p48 loose: 23
- large p32 loose: 27; large p64 0.6/0.8: 32
- huge p32 loose: 29->36 with postproc v2+buffer; huge p48 0.65/0.85: 35
- **huge p64 iou 0.6 stab 0.8: 36/42 <- chosen defaults**
- All 42 anchors land in exactly one polygon; 6 non-bijective = 3 pairs:
  - Greenland/Iceland: D12 puts Iceland's anchor ON Greenland's landmass
    (verified pixel RGB [154,120,20] = yellow land) -- unfixable faithfully
  - S.Africa/Madagascar: anchor in water nearer SA coast than island
  - Irkutsk/Mongolia: SAM strips misaligned with faint border in hatching
- Post-processing pipeline (candidates.py): size gates -> frame/border ->
  IoU dedup -> inset-box (legend) removal -> composite suppression ->
  name-pill removal (shape-based) -> ocean-blob removal -> fringe trim ->
  paint largest-first w/ remnant compaction -> 12px coastal claim.
- Coastal claim exists because D12 anchors island labels in the water.
- Tests pin bijection == 36 and the exact failed-id set {11,32,41,42,55,56}.

## Four-map verification (looked at every overlay)

- map 1 World Classic: 98 found / 42 expected, bijection 36/42 (three
  characterized pairs fail). Junk = title letters, decorative islands,
  NZ, Madagascar split. Boundaries crisp.
- map 79 Arctic Circle: 132/34. Real territories present after the
  ocean-rule annulus fix (grey-on-grey palette). Junk: central compass
  art, ARCTIC CIRCLE letters, region banners, decorative ship.
- map 100 Empires Med HD: 285/150. Most territories individually
  outlined; junk from title lettering and the bonus inset mini-map.
- map 34 Jungle of Despair: 475/60. Honest negative: SAM fragments the
  jungle hatching into hundreds of foliage masks. Flagged by count.

## Late fixes

- ocean-blob rule: ring is now an annulus 5-12 px out, measured against
  background eroded 7 px, so thin border-line gaps don't make bordered
  territories look ocean-surrounded (fatal on Arctic Circle).
- coastal_buffer_px 12 -> 14 (Britain's anchor is 13.6 px offshore).
  Earlier 36/42 secretly matched Britain to an "Iceland" text blob; the
  current 36/42 matches Britain to its real island polygon.

## Remaining

- suite run (background), 77-map run, commit, /tmp report.
