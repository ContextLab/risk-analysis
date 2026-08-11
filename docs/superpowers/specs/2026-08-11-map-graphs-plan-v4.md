# Map extraction — plan v4 (graphs first, outlines deferred)

**Date:** 2026-08-11
**Issue:** [#4](https://github.com/ContextLab/risk-analysis/issues/4)
**Supersedes for now:** v3's outline scope. v1 and v2 remain dead.

## Decision

**Skip territory outline parsing. Get the graphs correct.**

The segmentation architecture is **retained, not deleted** — `riskdyn/segment/` stays committed,
tested and working, because outlines may be wanted later for figures. It simply stops being on the
critical path.

## Why this is the right cut

Outlines were consuming the entire workstream — SAM tuning, mask filtering, over-production, coastal
halos, land masks, archipelago handling — and produced the three failed plans. Meanwhile:

- **The graph was always going to be manual.** Measured: shared-border adjacency precision plateaus
  at **0.90 at every dilation threshold** (6 false pairs at ≤1.4px against 29 true edges needing
  >1px — overlapping distributions, no threshold exists). Dropping outlines removes **zero** edge
  work.
- **Nothing downstream needs polygons.** Position strength and win probability need graph plus
  ownership; graph metrics need adjacency; playback can colour nodes; and issue #1's "vector,
  plottable at any resolution, with labeled territories and connections" is satisfied by the
  node-link renderer that already exists and works.
- **Region membership gets easier, not harder.** Sample the artwork's pixel colour at each node and
  cluster. That solves #4's original continent blocker as a side effect, with no segmentation.

What is given up: choropleth-style rendering of filled territories. That is a visualisation nicety,
not an analysis requirement.

## Scope

Per map, produce:

1. **Nodes** — one per playable territory, placed approximately inside it, carrying its name.
   Precision is deliberately loose: **nothing derives from node coordinates.** They exist for layout
   and human verification only. If anything downstream ever starts inferring from them, revisit this.
2. **Graph** — adjacency, authored rather than computed. Each edge marked **shared-border** or
   **route**, plus any special rule (one-way, conditional). Cylindrical wrap supported (map 1's
   Alaska–Kamchatka spans 801px).
3. **Regions** — per-territory membership, seeded by pixel colour at the node, cross-checked against
   the catalog's `num_regions`.
4. **Bonuses and special rules** — unchanged from v3 and still the hardest part, because there is no
   automated ground truth.

## Acceptance criteria

| # | criterion | check |
|-|-|-|
| **b** | every playable territory has exactly one node, correctly named | count equals catalog `num_territories`, **and** names verified individually |
| **e** | the graph is correct | every edge confirmed; no missing edges, no false edges |
| **f** | region membership is correct | distinct regions equals catalog `num_regions`; membership verified |
| **d** | bonuses and special rules are accurate | transcribed and independently confirmed |

Criteria **(a)** non-overlap and **(c)** no-non-playable-regions from v3 **drop out** — both were
artefacts of having polygons, and both were the ones the review judged unverifiable or already
failing.

`num_territories` remains **necessary but not sufficient**: the right count can hide a dropped
territory plus a spurious one. Names are the real check.

## Known traps, carried forward

- **Routes are not separable by style, even within one map.** Map 48's "Halley's Comet" is a white
  dashed streak identical to its real routes. Route classification is manual.
- **~20% of edges share no border** — 17 of map 1's 83.
- **Bonuses do not always attach to regions.** Map 100 prints numerals with no adjacent region name
  (association is by colour) and has ~9 per-territory or per-strait `+1` markers; its grey
  territories sit outside all 24 regions; map 77 has `num_regions=0`.
- **Prose rules are stored verbatim** with bounding box, never parsed into game logic.

## Workflow — unchanged and already built

`riskdyn/workbench/` is proven on map 1 and needs no rework for this scope:

- manual work lives in `data/authored/maps/<id>/annotations.json`, **committed to git**
- `python -m riskdyn.workbench.build <id>` regenerates derived artifacts and re-runs checks
- `report.json` states **pass / fail / unverified** per criterion and never implies a pass; "an agent
  looked at the overlay" is explicitly not human sign-off

Verification artifact per map: an overlay showing **named nodes and drawn edges** over the artwork.
An edge that shouldn't exist, or a missing one, is visible at a glance — which is what sets the pace
of ~3,900 edge decisions.

## Effort

~4,613 nodes and ~3,900 edges across 77 maps. Nodes are cheap and self-evidently checkable; edges
dominate and are irreducible.

Map 1 is **done** under this scope: 42 named nodes, 83 edges (70 shared-border, 13 route) matching
D12 ground truth exactly.

## Still open

D12 permission request sent 2026-08-10, unanswered; follow up **2026-08-24**. A grant would supply
adjacency, names and positions directly and make most of the edge work redundant. Proceeding
manually is a deliberate hedge against a refusal — but if a grant arrives mid-way, stop the manual
work immediately rather than finishing for completeness.
