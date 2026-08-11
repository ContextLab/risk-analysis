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

---

## SCOPE CHANGE 2026-08-11 (later same day) — the manual edge work is OFF

**Jeremy manually saved one D12 game page per map in his browser.** Each carries D12's own
`data-territory` / `data-adjacencies` / `data-x` / `data-y` / `data-name` markup, so names,
coordinates and every edge come free and exact — no reading of artwork, nothing to red-team.

Imported (`d544ac6`): **59 maps, 3,748 territories, 7,361 edges**, all 58 catalogued ones matching
`num_territories` exactly, plus map 9 (artwork but no catalog metadata).

Plan v4 budgeted ~3,900 edges across all 77 maps. The true count for 59 maps alone is **7,361** —
the estimate was low by ~2.4x, so the work avoided is correspondingly larger than advertised.

Saving is done **manually, by a logged-in member, in a browser**. `/game/` remains
robots-disallowed to the client and no code fetches it. Saved pages live in
`data/raw/saved_pages/` (gitignored — live session tokens, other players' names). Verified: no
token-like content and no unexpected keys reach `data/authored/`.

Workflow: `./.venv/bin/python -m riskdyn.workbench.import_page <id>` (`--status` for coverage).

### What the markup does NOT carry

**Continents.** That absence is this issue's original blocker and it does not move. Every imported
map has empty `regions`, so criterion (f) **fails** rather than pretending. Remaining work:

1. **Region membership** — sample artwork pixel colour at each node. Now easy: the coordinates are
   D12's own, so we sample at known-correct points rather than guessing.
2. **Bonuses and special rules** — read each map's legend. Still no automated ground truth.

### Open questions

- **Map 25 (Europe 1814 Advanced): 102 of 146 directed adjacencies are one-way**, every territory
  populated, undirected union sane (124 edges, degrees 2-8). Genuine one-way mechanic or a data
  convention? Edges are kept as the markup states them; the map is **not** silently symmetrized.
  `graphs.undirected_edges()` raises on asymmetry, which is the right default — map 25 needs a
  directed path or a verified decision.
- **19 maps still have no saved page**: 2, 10, 11, 33, 39, 44, 52, 53, 62, 63, 66, 68, 73, 86, 88,
  90, 98, 102, 103. Jeremy is pursuing them.

### Vision calibration (measured, so the fallback is a known quantity)

Blind reads of map 1 — image only, fixture access forbidden, isolation verified — scored against
D12's markup:

| reader | edges | precision | recall | names |
|-|-|-|-|-|
| fable | 80 | **1.000** | 0.964 | 42/42 exact |
| opus | 81 | **1.000** | 0.976 | 42/42 exact |
| union | 82 | **1.000** | **0.988** | 42/42 exact |

Neither invented an edge; both err only by omission, which the overlay makes visible. Inter-rater
Jaccard was 0.963, close to each reader's true recall — so **agreement approximates accuracy**.
But **both missed Greenland-Ontario**: shared blind spots are real, and agreement bounds trust from
above rather than establishing it.

**This is a ceiling, not an expectation**: map 1 *is* the standard Risk board, memorized by every
model. Use these numbers only for the 19 uncovered maps, and take the union of two readers.

### Schema defect found during calibration

**Territory-to-region is many-to-many, not scalar.** Map 7 prints two legends — 6 colour "Region
Bonuses" plus 6 overlapping "Additional City Bonuses" (6+6 = the catalog's 12) — and has
territories in no region at all. The scalar `region_id` cannot represent this: criterion (f) would
score a perfectly authored map 7 at 6 of 12. Fix in progress: `region_ids: []` per territory.

### Correction to a trap recorded above

Map 100's legend **does** print region names beside most numerals (Umayyad 1, Idrisid 3, Bulgar 4,
Byzantine 3, Khazars 1, Nedjed 3, …); only the scattered `+1` markers are nameless. Both prose
rules are plainly legible. Bonus transcription there is easier than previously recorded, not harder.
