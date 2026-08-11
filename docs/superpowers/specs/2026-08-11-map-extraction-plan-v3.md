# Map extraction — plan v3 (per-map, one-time)

**Date:** 2026-08-11
**Issue:** [#4](https://github.com/ContextLab/risk-analysis/issues/4)
**Supersedes:** v1 (partition-the-land) and v2 (label-containment), both killed on evidence.

## The reframing

Scope clarified 2026-08-11: **each map needs to be processed exactly once.** This is a one-time
extraction job producing 77 durable artifacts, **not** a reusable pipeline that must run over every
map in a single pass.

This inverts the design. v1 and v2 both died trying to find a rule general enough to cover 77
hand-drawn illustrations that share no convention — offshore labels, decorative dashed rings,
planets instead of landmasses, a Sun larger than any territory, mini-map insets that tile like real
territories. **There is no such rule, and we no longer need one.** Per-map handling, per-map
constants, and per-map manual correction are all now explicitly permitted.

The previous non-goal "no per-map hand-tuned constants" is **deleted**. It was the constraint doing
the damage.

## Acceptance criteria

Per map, the extraction is correct when:

| # | criterion | how it is checked |
|-|-|-|
| **a** | territory outlines **do not overlap** each other | pairwise polygon intersection area == 0 (within tolerance) |
| **b** | **all** playable territories are included | count equals catalog `num_territories`; every territory identified by name |
| **c** | **no** non-playable regions are included | no mini-map, legend, decoration, ocean, or scenery among the outlines |
| **d** | troop-bonus rules are **accurate** | per-region bonus values, plus any special rules, transcribed and verified against the artwork |

Geometry may be **approximate** — outlines need to be right about *which* territory they are and
where it roughly sits, not pixel-perfect. Criterion (d) is the one with no tolerance: bonuses drive
game scoring, so a wrong number silently corrupts every downstream position-strength metric.

Note (b) and (c) together make the catalog's `num_territories` a genuine equality check rather than
the warning signal it was under v1/v2 — exactly N territories, no more, no fewer.

## Also required: graph structure

Beyond outlines, each map needs its **territory adjacency graph**, including connections that share
no border — the dashed/dotted sea routes (Alaska–Kamchatka, Brazil–North Africa). Shared-boundary
adjacency alone is insufficient and would produce a wrong graph.

Map-specific connection rules count as "unique rules" and must be captured: one-way connections,
teleports, or bonus conditions such as map 100's *"+1 for controlling a grey territory together with
an adjacent region"* and *"+5 for controlling all 5 Holy Cities"*.

## Method: whatever works, per map

No single technique is mandated. Available, in rough order of leverage:

1. **SAM masks** — boundary quality is good (median IoU 0.984) and already computed. Best starting
   geometry where territories are colour-distinct.
2. **A vision pass reading the map as a person would** — which text names a playable territory,
   which is a sea or a region caption or attribution; what the legend's numbers mean; which dashed
   lines are routes rather than decoration. This is judgement, and it is what defeated every
   style-based heuristic.
3. **Ground truth where it exists** — map 1 has 42 exact anchors, names, and 83 edges from D12's own
   markup. It stays authoritative for that map and is the calibration case for everything else.
4. **Manual correction** — expected, not a failure mode. Every map gets human verification anyway.

## Structure of the work

Per-map, and therefore **parallel across maps**. A map is done when its artifacts satisfy (a)-(d)
and a human has looked at its overlay.

Per-map artifacts under `data/processed/maps/<id>/`:

- `territories.svg` — non-overlapping outlines, one path per territory (subpaths for archipelagos)
- `territories.json` — id, name, polygon(s), centroid, region
- `graph.json` — adjacency, with each edge marked as shared-border or route, and any special rule
- `bonuses.json` — per-region bonus values, plus special rules **transcribed verbatim** alongside any
  structured interpretation
- `report.json` — the (a)-(d) checks, pass/fail per criterion
- `overlay.png` — for human verification

## What "done" is not

- Not a single automated pass over all 77 maps.
- Not pixel-perfect geometry.
- Not a general algorithm. If map 48 (planets) needs entirely different handling from map 1
  (continents), that is an acceptable outcome, not a defect.

## Risks that survive the reframing

- **Criterion (c) is the subtle one.** Excluding a mini-map inset or a decorative Sun requires
  knowing it is not playable — which is semantic, not geometric. This is where v1 died and it does
  not go away; it is merely now permitted to be solved per-map.
- **Bonus accuracy has no automated ground truth.** Nothing checks a transcribed bonus except
  reading the artwork. Verification must be explicit and independent of the transcriber.
- **Sea routes are unsolved.** No prior stage attempted them, and they are required for (d)'s graph.
- **77 maps × manual verification is real work**, and its throughput is set by how legible the
  overlays are.

---

## Adversarial review of v3 (2026-08-11) — measured findings

**Verdict: revise.** The per-map reframing is right; two of four criteria are not tests.

- **Adjacency cannot be derived from approximate geometry.** Measured on map 1 (36 territories with
  unique anchors, 65 of 83 edges): shared-border recall by dilation tolerance d=1px → 36/65, d=2 →
  54/65, d=3-30 → ~57-62/65. **Precision plateaus at 0.90 at every threshold.** Six false pairs sit
  at ≤1.4px while 29 true edges need >1px — the distributions overlap, so no threshold exists.
- **~17 of 83 edges (20%) are non-border**, not the ~10 assumed. Clean gap in the data: nothing
  between 4px and 12px separation.
- **Map 1 wraps cylindrically.** Alaska-Kamchatka measures 801px. Never mentioned in any plan; it
  breaks polyline tracing.
- **`num_territories` is necessary, not sufficient.** Dropping a real territory while admitting one
  mini-map tile still yields N=42. It cannot support criterion (c) at all.
- **Routes are not separable by style even within one map.** Map 48's "Halley's Comet" is a white
  dashed streak identical to the white dashed routes drawn on Earth in the same image.
- **`bonuses.json` as specified cannot hold real data** — map 100 prints bonus numerals with no
  adjacent region name, plus ~9 scattered per-territory/strait `+1` markers, and its grey
  territories sit outside all 24 regions.
- **Map 1's fixture has `region_id` all 0**, so the calibration map cannot calibrate (c) or (d).
- **Criterion (a) already fails on the shipped map-1 artifact**: 107px of polygon overlap.
- **Effort inverts**: ~4,613 territories (mean 60, max 150) but **~3,900 edges**. Adjacency
  dominates, and there is no correction round-trip — hand-editing `territories.svg` regenerates
  neither `graph.json` nor `report.json`, and nothing records who verified what.

## The strategic consequence

Adjacency, names and label positions are **exactly what D12's markup already provides** via
`data-adjacencies` / `data-name` / `data-x` / `data-y`. We have them for map 1 and would have them
for all 77 with data-use permission (requested 2026-08-10, unanswered; follow-up 2026-08-24).

So the ~3,900 manual edge decisions are largely a **workaround for not having permission**, not
inherent work. Segmentation remains necessary for *outlines* regardless — markup carries no shapes —
and bonuses need reading the artwork either way.
