# Issue #4: territory region membership is many-to-many (`region_ids` list)

## Why

Map 7 ("Oregon Cities", catalog `num_regions` 12) prints two overlapping
legends on the artwork: "Region Bonuses" (6 colour groups) and "Additional
City Bonuses" (6 more, each worth 1). "Tigard 1"/"Tigard 2" are in the green
colour region AND together form the Tigard city bonus, so a territory can be
in 2 regions; Bull Mountain, King City, Ladd Hill, Saint Paul, Mulino, etc.
are in NO region. Map 100 has the same shape (grey territories outside every
region). The scalar `region_id` in annotations schema v2 could not express
this, and criterion f would have scored a perfectly authored map 7 at 6 of
12 distinct regions.

## What changed

- Schema v2 territories now carry `region_ids: [..]` (0, 1, or many; `[]`
  means no region). The old scalar key is REJECTED loudly in
  `graph_build.validate_annotations` -- no compatibility shim, a silent
  `[rid]` coercion is how a wrong assignment would survive unnoticed.
- `graph_build.py`: validation (list required, no duplicate ids within a
  territory, every id defined in `regions`), nodes.json writes `region_ids`,
  criterion f = distinct used ids == catalog num_regions AND every defined
  region used; `region_ids: []` legal (reported, never a failure); empty
  `regions` -> fail (genuinely absent, not unverified). Report carries
  `n_territories_multiple_regions` / `n_territories_no_region` -- the two
  numbers a human checks against the artwork. Overlay nodes coloured by
  FIRST region id (white = none); title reports the multi/none counts.
- `import_page.py` writes `region_ids: []` (never scalar, never `[0]`).
- `build.py` segmentation path: `_region_tables` membership is now
  tid -> list; a territory may appear in several regions' `territory_names`
  (dup within one region still raises); territories.json gets `region_ids`.
- `scripts/convert_region_ids_to_lists.py`: one-off migration, run on all
  59 `data/authored/maps/*/annotations.json` (3748 territories: map 1 got
  `[rid]`, the 58 imports got `[]`; importer notes prose updated too).
  Idempotent; refuses to guess on both-keys/non-integer.

## Verification

- `./.venv/bin/python -m pytest tests/ -q -m "not network"`: 226 passed
  (baseline 217 + 9 new in tests/test_graph_build.py: two-region territory
  counted once per region; f pass with `[]` memberships when all regions
  used; 6-of-12 fails (map 7 defect); regions `[]` -> f fail; scalar key /
  missing list / dup-in-territory rejected; import yields `[]`).
- Rebuilt end to end: map 1 -> b=pass e=pass f=pass d=unverified
  (0 multi, 0 none); map 100 -> f=fail (0 multi, 150 none).
- Overlay for map 1 visually inspected (title counts + region-coloured
  nodes).

## Open items

- Map 7 itself still needs its 12 regions authored from the artwork; the
  schema can now express the overlap.
- `riskdyn/maps/model.py` (`MapTopology.Territory.region_id`, scalar, from
  D12 markup's absent `region` attr, default 0) is a separate upstream layer
  and was deliberately left alone.
