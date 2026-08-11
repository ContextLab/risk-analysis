# 2026-08-11 — issue #4: per-map extraction workbench + artifact schema

Session scope: build the workbench and artifact schema for the 77-map
extraction (plan v3 + its adversarial review), proven on map 1.

## What exists now

- `riskdyn/workbench/`: provenance.py (human-verification gate — generators
  can never set human_verified; apply_verification refuses agent names),
  overlap.py (criterion (a) measure via shapely + deterministic resolver,
  quantized to the artifact's 0.1 px precision so the zero is measured on
  what is written), bonuses.py (schema validation covering map-100/77
  realities: region_id-null numerals by colour, per-territory/per-edge +1,
  prose verbatim never parsed, regions=[] valid), graphs.py (edge list from
  ground truth ONLY; geometry proposes kind per known edge, dead band 6-12 px
  -> "unknown"; wrap measured at shifts {-w,0,+w}), checks.py ((a)-(d), each
  pass/fail/unverified), build.py (build CLI, corrections, SVG round-trip).
- `data/authored/maps/<id>/annotations.json` — committed home of ALL manual
  work. data/processed stays gitignored and fully regenerable.
- Correction round-trip: edit annotations.json (or hand-edit territories.svg
  then `--from-svg`), re-run `./.venv/bin/python -m riskdyn.workbench.build 1`.
- `tests/test_workbench.py`: 18 tests incl. real map-1 e2e on cached SAM
  masks (skips only if local data absent).
- TerritoryShape gained `source_label` so shapes map back to seed groups.

## Map 1 state (honest)

- a: PASS, 0.0 px^2 (pre-resolution raw extraction: 1.137 px^2 / 2 pairs).
  The review's historical "107 px" was NOT reproducible with current code
  (old artifact was gitignored + overwritten; regenerating from the same
  cached masks gives 1.137).
- b: PASS, 42/42 with name bijection via seed claims (count alone documented
  as insufficient).
- c: UNVERIFIED (automated sub-checks pass; agent vision pass of overlay.png
  looks right, incl. legend mini-map not admitted; human sign-off pending).
- d: UNVERIFIED. Bonuses NA5 EU5 AS7 SA2 AF3 AU2 read from legend crop (4x
  zoom, unambiguous numerals, association by colour — legend has no region
  names). Region membership 9/4/7/6/12/4 assigned by agent from artwork
  colours; fixture region_id all 0 so nothing validates it automatically.
- graph.json: 83 edges, 70 proposed shared-border / 13 proposed route / 0
  unknown; Kamchatka-Alaska (60-66) wraps=true. All proposed, none confirmed.
- Greenland+Iceland shared one SAM mask -> authored split_components
  correction.

## Open items / next agent

- Human needs to confirm overlay + bonuses via annotations.json
  verification block (then c/d flip to pass on rebuild).
- The 13 route proposals need per-edge confirmation (vision or human) via
  edges.confirmations.
- Other 76 maps: need a topology source (D12 permission pending — see
  d12-permission-request.md) or authored edge lists; build fails loudly
  without one, by design.
- Schema reference for other agents: /tmp/riskdyn-workbench.md (also
  summarized in module docstrings).
