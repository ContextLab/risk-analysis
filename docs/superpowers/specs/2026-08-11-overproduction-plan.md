# Solving segmentation over-production — plan v2 (anchor-driven)

**Date:** 2026-08-11
**Issue:** [#4](https://github.com/ContextLab/risk-analysis/issues/4)
**Status:** v2, revised after adversarial review demolished v1

## The problem

| map | expected | SAM polygons | ratio |
|-|-|-|-|
| 1 World Classic | 42 | 85 | 2.0× |
| 79 Arctic Circle | 34 | 139 | 4.1× |
| 100 Empires of the Med HD | 150 | 283 | 1.9× |
| 34 Jungle of Despair | 60 | 497 | 8.3× |

Buffer=0 bijection on World Classic: **33/42**.

## v1 was wrong. Recording why, so it is not re-proposed

v1 proposed selecting the subset of SAM masks that best **partitions the land**. Adversarial review
tested that against the artwork and found the central assumption **false**, and false precisely on
the hard maps:

- **Map 48 "Solar System"** — no land at all. Territories are planet discs and an asteroid belt; the
  largest non-background blob is the **Sun** (72,373 px), which is decoration. A max-land-coverage
  objective selects the Sun first.
- **Map 100** — a self-labelled "- Mini map -" inset is **15.3% of the image**, land-coloured and
  territory-tiled. Map 1 has the same inset at 2.4% of its land.
- **Map 79** — "Umingmak Nuna" and "Franz Josef Land" are sea-ice territories whose bodies are
  ocean-coloured. A land mask excludes real territories.
- **Map 34** — **no ocean at all**; land is ~96% of the image at every tolerance, so "partition the
  land" is trivially true and constrains nothing.

The land mask itself was the deeper flaw. Modal share of the border ring is 12% / 11% / 10% on maps
79 / 100 / 34 — those borders are decorative frames, not ocean. Land fraction across tolerance 40→200
swings .88→.17 (map 1) and .98→.69 (map 100), and **no single tolerance is sane on even two probe
maps**. As the review put it: one binary mask per map is one hand-tuned constant per map, renamed.

That also collapsed v1's cross-check: approach B (border-network faces) consumed the same land mask,
so A and B would fail together while appearing to agree — manufactured confidence.

One v1 worry was retracted on evidence: label anchors sitting offshore. Only 2 of World Classic's 42
anchors are >3px from land, max 3.3px.

## v2: anchors drive selection

**Every territory has its name printed on it.** That is the seed the geometry should be selected
against, and it is available on all 77 maps — unlike ground truth, which exists for one.

Objective, replacing "cover the land":

> Select the set of SAM masks such that **each selected mask contains exactly one territory-label
> anchor**, and **each anchor is contained in exactly one selected mask**.

This is a bipartite matching between anchors and masks, not a coverage maximisation.

Why it is better:
- The Sun, both mini-map insets, the Viking ship, the cathedral and the painted scenery contain no
  territory label, so they are excluded **structurally** rather than by bespoke rules.
- It works on map 48 (planets) and map 34 (no ocean), where no land mask can.
- The diagnostics become load-bearing: *anchors unmatched* and *masks anchorless* directly measure
  the thing we care about. Under v1, both "uncovered land" and "overlap" read healthy while the
  pipeline selected the Sun.

## Consequence: text detection moves into Stage 1

v1's staging deferred OCR to Stage 2. That was backwards — the semantics must **inform** the
geometry, not follow it. Stage 1 now needs text **localisation** (centroids and boxes). Full
transcription stays in Stage 2; we need to know *where* labels are before we need to know what they
say.

### The new hard problem, stated honestly

Not all text is a territory label. Observed classes:

| class | example | must be |
|-|-|-|
| territory name | "Kamchatka", "Umingmak Nuna" | anchor |
| region caption | "NORTH AMERICA", "SIBERIA" (map 79) | rejected |
| sea name | "Atlantic Ocean", "Black Sea" (map 100) | rejected |
| title / dates | "World Classic", "At the time of the Byzantine-Arab wars" | rejected |
| attribution | "Hoodlum 2019", "Map created by Dima" | rejected |
| inset contents | mini-map numerals and names | rejected |
| rules prose | "+1 for controlling a grey territory…" | rejected (kept verbatim, stage 4) |

Multi-line labels ("NW / Territory", "Papua New / Guinea") must group into **one** anchor, not two.

This is a better-posed problem than classifying arbitrary blobs — text class is legible to a vision
model in a way "is this SAM mask a territory" is not — but it is the new principal risk and must not
be waved through.

**Consistency check available for free:** the catalog gives each map's exact `num_territories`.
Detecting materially more or fewer territory-label anchors than that is a loud, per-map signal that
classification went wrong. Used as a **warning**, never as a filter or a truncation.

## v2 was also wrong. Post-mortem

A second adversarial review tested v2's core claim — that each territory carries a printed label
inside it — against the artwork, and it is **false on the easy map**:

- **8 of 42** World Classic territory names are printed **entirely in open ocean**, off their own
  landmass: Iceland, Britain, W. Europe, Central America, Japan, Madagascar, Indonesia, Papua New
  Guinea. Five more (Alaska, Quebec, Kamchatka, Venezuela, Siam) straddle a coast or border.
  On map 79, Svalbard, Nova Zembla, Franz Josef Land, Severnaya Zemlya and Kitlineq sit on open ice.
- **Archipelago territories are one label over N disconnected masks.** Neither plan mentioned
  multi-polygon territories. A territory is not necessarily one polygon.
- **D12's 42 anchors are render centroids, not text positions** — Eastern US's anchor sits nearer
  the printed words "Western US". Ground-truth anchors and printed labels are different objects and
  must not be conflated.
- **No perfect matching exists.** Anchors in no mask, one mask holding several anchors, and one
  anchor needing several masks all occur, so strict bipartite matching degrades to max-cardinality
  plus an arbitrary leftover policy — the arbitrariness v1 died of.
- **Text class is genuinely ambiguous, not merely hard.** On map 100 a single italic style covers
  sea names ("Northern Sea"), a region caption ("western Slavs"), *and* territory names ("Rome",
  "Constantinople"). On map 79, caps covers both region captions and decoration. The obvious cue
  "text over water ⇒ sea name" is exactly wrong for sea-ice territories and for Iceland/Britain.

The ≥38/42 gate is unreachable as specified, since ≥8 map-1 anchors have no containing land mask.

## The pattern across both failures

Both plans assumed a **clean geometric relation** the artwork does not honour. These are
human-designed illustrations: label placement follows aesthetic judgement — offshore labels for
small islands, leader lines, text overflowing into neighbours — not a rule a predicate can express.

What is actually reliable, after two reviews:

| signal | reliability |
|-|-|
| SAM boundary quality | **good** — median mask/polygon IoU 0.984 |
| determinism | **good** — identical across processes |
| D12 render anchors as interior points | good, but **map 1 only** (2/42 >3px from land, max 3.3px) |
| every territory's name appears *somewhere* | good |
| catalog `num_territories` | exact |
| any geometric label↔territory rule | **poor** |

## Open measurement, deciding v3

Before more selection code is written, quantify on World Classic:

1. distance from each ground-truth anchor to its **correct** polygon, versus to the nearest
   **incorrect** one — the gap between those distributions determines whether proximity can
   discriminate at all;
2. how many anchors are inside exactly one mask, several, or none;
3. how many territories plausibly need more than one polygon.

If proximity cannot separate correct from incorrect, no geometric selector will work, and the
architecture should change: SAM for boundaries, a **vision model for assignment** (answering "which
region is Iceland?" using leader lines, colour and context the way a person does), and the
human-verification workflow already agreed for all 77 maps. In that case the deliverable is
explicitly a tool that gets most of the way and makes the remainder fast to correct — not an
automated pipeline.

## Success gates, all at buffer=0

| metric | now | target |
|-|-|-|
| World Classic bijection | 33/42 | ≥ 38/42 |
| anchors detected vs catalog `num_territories` | not measured | within ±10% on maps 1, 79, 100 |
| masks selected with no anchor | 50 (map 1) | 0 by construction |
| anchors with no mask | not measured | reported per map |

**Map 34 (Jungle of Despair) is explicitly out of scope for a target.** Review judged ≤120 polygons
unachievable: photorealistic foliage is painted *across* the borders, and its only usable signal is a
border network that the artwork actively obscures. A loud, honest failure there is the accepted
outcome. A quiet plausible-looking wrong answer is not.

## Non-goals

- No truncation to `num_territories` — tested and rejected: top-42-by-area recovers only 32 of 39
  real polygons.
- No global land/ocean mask as a selection gate. It may still inform scoring, never gating.
- No per-map constants. Any threshold must be derived from the image or justified across all four
  probe maps.
