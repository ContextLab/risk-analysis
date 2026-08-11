# 2026-08-11 — issue #4: legend/region schema v3 + merge_legend gate

## What was built

- `riskdyn/workbench/legend_schema.py` — schema v3 validator/loader.
  Splits the pilot's single confidence scalar (bonus.confidence /
  association.confidence / per-member confidence), makes `name` an object
  `{text, provenance, spans_region_ids, confidence, note}` with provenance
  `printed | printed-unbindable | inferred | none-printed`, and makes
  members objects (bare strings REJECTED, no silent upgrade).
  `layer: base|overlay` legalizes map 7's 44 memberships over 35
  territories; two base regions claiming one territory is an error.
  `map_specific` is never required, and when present every key must have a
  prose entry in `map_specific.documentation`.
- `riskdyn/workbench/merge_legend.py` — `python -m riskdyn.workbench.merge_legend <id> [--force]`.
  Reads `data/authored/maps/<id>/legend-map<id>.json` + processed
  `region_sample.json`, writes `regions` + per-territory `region_ids` into
  annotations.json, gated by a per-territory colour cross-check.
- `scripts/convert_legends_v3.py` — one-time migration of the pilot drafts
  (scratchpad legend-map{1,7,100}.json) to v3, applying red-team D1–D4
  corrections; also generates map 25's legend (map_specific unit classes
  derived from `data/raw/saved_pages/25.html`).
- Tests: `tests/test_legend_schema.py` (13), `tests/test_merge_legend.py`
  (16). All stage COPIES of real data into tmp trees; repo data never
  mutated by tests.

## The gate rule (why 23 on map 100)

A base member agrees iff its colour cluster's majority (plurality) base
assignment equals its legend region, with two refinements:
- ties resolve deterministically (no-region first, then smallest region
  id) and are flagged `ambiguous_majority`;
- a member alone in a singleton cluster DISAGREES whenever its region has
  other members (colour isolates it from every claimed region-mate).
Overlay members get `agrees_with_cluster: null` (their paint IS the base
colour; a boolean would be fabricated).

Map 100 (corrected file): exactly 23 conflicts = 6 Umayyad members
(share region 22's exact fill #8ca05a) + 6 r23-vs-r18 + 6 r10-vs-r6 (two
evenly-split clusters) + Leon (clusters with Hamdanid) + 4 singletons
(Rome, Benevento, Castile, Billungermark). The four corrected pilot
errors (Chernigov/Kerch/Edessa/Alamania) now AGREE with their clusters.
NOTE: the brief's "four are known legend errors" phrasing described the
red-team's pre-correction count; post-migration the 23 are all
unadjudicated. Refusing is correct; nothing was resolved.

Map 7: refuses with exactly the 4 clustering artefacts the red-team
documented (Metzger, Butteville, Donald, Aurora — parchment label
sampling). Map 1: merges clean (0 conflicts).

## Human guard

`_member_is_human_protected`: human sign-off in
`verification.regions_confirmed` (via graph_build's `_human_signoff`,
agents never count) OR territory `note`/`evidence` matching
`HUMAN_SIGNOFF_RE` (`human ... verified/confirmed/sign-off`). A protected
member whose membership would change refuses the merge — `--force` does
NOT override. No sign-off exists yet on any map; guard tested via staged
copies.

## Deliberate decisions

- Did NOT merge map 1 into the repo's annotations.json: existing tests
  (test_graph_build/test_regions/test_workbench) assert the current
  authored state (regions carry `territory_names` + conventional continent
  names from the earlier vision read). The merge tool drops printed-nowhere
  names (none-printed => null), which is schema-faithful but conflicts
  with that asserted state. Applying merges into the repo is a separate,
  human-gated step.
- Migration does not propagate pilot notes the red team proved false
  (D6 fabricated sampling stories; D7 wrong open-water list); replaced
  with measured facts, cited to the red-team review.
- Map 25 classes recorded as class_0/1/2 (18/18/19; 146/146 directed
  edges, 0 violations); binding to cavalry/artillery/infantry is an
  explicit `unresolved` gap (only a partial icon spot-check exists).
- `data/processed/**` is gitignored; region_conflicts.json artifacts are
  local only.

## Status / next steps

- Full suite: 238 pre-existing + 29 new pass, 5 network-marked deselected.
- Untracked `code/scripts/d12_index_games.py` predates this session; left
  alone.
- Next: human adjudication of the 23 map-100 and 4 map-7 conflicts, then
  merge; author legends for the remaining 74 maps; decide whether merged
  annotations should also carry conventional (unprinted) region names via
  an explicit `inferred` name rather than the vision-read flat names.
