# Issue #4 handoff — 2026-08-11

## Decision taken

**Manual/vision-assisted adjacency for all 77 maps**, independent of D12 permission. Chosen after a
measurement showed shared-border adjacency precision plateaus at **0.90 at every dilation
threshold** (6 false pairs at ≤1.4px vs 29 true edges needing >1px — overlapping distributions, so
no threshold exists). Adjacency cannot be derived from geometry.

Effort: ~4,613 territories, **~3,900 edges** across 77 maps. Adjacency dominates, not outlines.

## What exists now

`riskdyn/workbench/` — per-map extraction workbench, proven on map 1. 178 tests.

- `data/authored/maps/<id>/annotations.json` — **durable manual work, committed to git**
- `python -m riskdyn.workbench.build <id>` regenerates all derived artifacts and re-runs checks
  (`--from-svg` imports hand-edited outlines)
- Derived per map: `territories.svg`, `territories.json`, `graph.json`, `bonuses.json`,
  `report.json`, `overlay.png`

Map 1 status: **(a) pass** (0.0 px² overlap, shapely-verified), **(b) pass** (42/42 with name
bijection), **(c) unverified**, **(d) unverified**. `graph.json` carries all **83 edges** — 70
shared-border, 13 route — matching D12 ground truth exactly.

The schema reports pass / fail / **unverified** per criterion and never implies a pass. "An agent
looked at the overlay" is explicitly not human sign-off.

## Critical schema facts for the other 76 maps

- `bonuses.json` must hold: per-region values; **numerals printed with no adjacent region name**
  (map 100 — association is by colour, needs an unresolved state); **per-territory/per-strait `+1`
  markers** (~9 on map 100); **territories outside every region** (map 100 grey; map 77 has
  `num_regions=0`); and **prose rules verbatim** with bounding box, never parsed into logic.
- `graph.json` marks each edge **shared-border** or **route**, and supports **cylindrical wrap** —
  map 1's Alaska–Kamchatka spans 801px because the map wraps.
- `num_territories` is **necessary but not sufficient**: dropping a real territory while admitting a
  mini-map tile still yields the right count.
- Routes are **not separable by style even within one map** — map 48's "Halley's Comet" is a white
  dashed streak identical to its real routes. Route classification is manual.

## Next

1. Fan out over the remaining 76 maps in batches, one agent per batch, per-map verification.
2. Each map needs human sign-off before (c)/(d) can move off `unverified`.
3. Red-team the batch output — every plan and product in this workstream that went unreviewed
   contained a defect.

## Still blocked on D12

Permission request sent 2026-08-10, unanswered. **Follow up 2026-08-24.** Phases 2-6 of the main
spec (board reconstruction, position strength, conversation pipeline, paper) remain gated on it.
Note that a grant would supply adjacency/names/positions directly and make most of the manual edge
work redundant — the choice to proceed manually was made with that known.

---

## SCOPE CHANGE 2026-08-11 — read this first

**Territory outline parsing is deferred. Graphs are the target.** See
`docs/superpowers/specs/2026-08-11-map-graphs-plan-v4.md`.

`riskdyn/segment/` is **retained, not deleted** — committed, tested, working. It may be revisited
for figures later. It is simply off the critical path.

Per map now: named nodes (approximate placement), authored adjacency graph, region membership via
pixel colour at the node, and bonuses/special rules. Criteria (a) non-overlap and (c)
no-non-playable-regions drop out with the polygons.

Dropping outlines removes **zero** edge work — adjacency was always manual — and makes region
membership easy, which resolves #4's original continent blocker as a side effect.

Map 1 is already complete under this scope: 42 named nodes, 83 edges (70 shared-border, 13 route),
matching D12 ground truth exactly.
