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

---

## STATE AT END OF 2026-08-11

**Topology: DONE for all 78 maps** (77 catalog + map 9). 4,643 territories, 9,019 edges, exact from
D12's markup. Commit `d544ac6` / `ab062af`. No vision anywhere in that chain.

**Schema v3** (`b3acbe0`): split confidence (bonus / association / per-member), name provenance
(`printed` | `printed-unbindable` | `inferred` | `none-printed`), object members with per-territory
evidence, base/overlay layers, and `map_specific` as a documented escape hatch for genuinely unique
maps.

**merge_legend gate**: recomputes `agrees_with_cluster` itself, refuses on disagreement (exit 2),
writes `region_conflicts.json`. Status: map 1 merged (f=pass); map 7 refuses (4); map 100 refuses
(23); map 25 out of scope until its legend regions are authored.

### Why the gate exists — do not weaken it

The 3-map pilot's four map-100 errors (Chernigov, Kerch, Edessa, Alamania; two changed a bonus
value) left the totals reconciling identically at 141 + 9 = 150. **The aggregate count check cannot
detect a swap.** Only the per-territory colour cross-check found them.

Map 100's 23 conflicts decompose as **4 colour-isolated singletons** (Castile, Billungermark,
Benevento, Rome — artifacts, same class as map 7's Metzger/Aurora/Butteville/Donald) plus **19
genuine disagreements** needing the artwork. Only 1 of 23 involved an unreliable sample.

### Map 25 — adjacency ≠ attack legality

Unique among all 78: 102 of 146 directed adjacencies are one-way. Legend prints
`CAVALRY / CANNOT ATTACK / ARTILLERY / CANNOT ATTACK / INFANTRY / CANNOT ATTACK / CAVALRY`. A
three-class cyclic model fits **146/146 with zero violations** (18/18/19), reproduced independently
twice. Stored as `class_0/1/2` — the structure is proven, the binding of a class to a printed unit
is NOT (partial icon spot-check only).

**Downstream consequence:** any metric treating adjacency as "where I can attack" is wrong on this
map. 124 undirected edges govern movement; a directional rule governs attacks.

### Vision calibration — why topology is not read from artwork

| map | inter-rater Jaccard |
|-|-|
| 1 World Classic (standard Risk board, memorized) | 0.963 |
| 7 Oregon Cities (unfamiliar) | **0.694** (routes: **0.526**) |

Names agreed 35/35 and 42/42. **Vision reads text reliably and topology unreliably.** The map-1
score was recitation, not reading. Vision is used ONLY for legends, names and bonuses.

### Next

1. Adjudicate map 100's 19 conflicts against the artwork (the 4 singletons are artifacts).
2. Fan the legend read out over the remaining 75 maps, gate every one.
3. **Human sign-off on bonuses.** Criterion d is `unverified` on all 78 and cannot move without a
   person. An agent transcribing a legend is explicitly not sign-off, and bonuses have no
   automated ground truth anywhere.

### Schema limits still open

Free-floating printed labels (map 100's "Map created by Dima" lives in `unresolved`), rule-to-
instance linkage, a typographic evidence category, and class→unit binding without a full icon read.

### Loose ends

- `code/scripts/d12_index_games.py` — untracked, awaiting Jeremy's call. It bulk-scans
  `/api/game/<id>` with a spoofed browser UA, 5 threads and no robots/permission gate, bypassing
  every safeguard `D12Client` enforces. The ToS prohibits this without written permission, still
  unanswered. Nothing in the repo depends on it.
- **D12 permission follow-up due 2026-08-24.** It matters far less now (the topology it would have
  granted is already collected) but still gates phases 2-6 of the main spec, where the actual
  conversation-dynamics analysis lives.

267 tests pass offline.
