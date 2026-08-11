# Map segmentation — design

**Date:** 2026-08-10
**Issue:** [#4](https://github.com/ContextLab/risk-analysis/issues/4)
**Status:** scoped, staged implementation

## Goal

Recover, for all 77 D12 maps, from artwork alone: accurate SVG outlines per territory, accurate
label positions and names, region (continent) membership, adjacency, and any special rules printed
on the map.

D12's markup gives adjacency, names and label coordinates — but only on `/game/<id>` pages, which
`robots.txt` disallows and which we have permission for on exactly **one** map. The artwork under
`/assets/img/maps/<id>.large.jpg` is robots-allowed and already downloaded, so it is the only
source that covers the catalog.

## What the artwork actually contains

Sampled two maps spanning the difficulty range.

**World Classic (map 1, 42 territories, 6 regions)** — the easy case:
- Regions are flat, well-separated colours (yellow/red/blue/orange/green/magenta)
- Territory borders are crisp darker lines within a region's colour
- ~10 **dashed sea routes** carry adjacencies that share no land border (Alaska–Kamchatka,
  Brazil–North Africa, Greenland–Iceland, …)
- A legend inset encodes continent bonuses as numerals: 5, 5, 7, 2, 3, 2
- Names are printed **abbreviated** relative to canonical: "NW Territory" vs "Northwest Territory",
  "Western US" vs "Western United States", "N. Europe" vs "Northern Europe"

**Empires of the Mediterranean (map 100, 150 territories, 24 regions)** — the hard case, and the
one that sets the bar:
- Region colours are subtle and numerous — many near-identical greens, tans, browns
- A decorative painted background (cathedral, mosque, mountains) bleeds through with transparency
- Dotted sea routes woven throughout the Mediterranean
- Text that is *not* a territory name: italic sea names ("Atlantic Ocean", "Black Sea"), a title
  block, dates, "Map created by Dima"
- **Prose special rules**: "+1 for controlling a grey territory together with an adjacent region",
  "+5 for controlling all 5 Holy Cities", plus per-region bonus numerals and scattered "+1"
- **Grey territories are a distinct game mechanic**

## The variety is the hard problem

Measured across all 77 images, not inferred from samples:

| property | range |
|-|-|
| mean saturation | **26 – 218** (near-greyscale to vivid) |
| distinct coarse colours | 47 – 202 |
| mean brightness | 84 – 197 (dark to bright) |
| edge density | 11 – 37 (flat fills to heavy hatching) |

A third sampled map, **Arctic Circle (79)** — the least saturated of all 77 — breaks assumptions
that both earlier samples shared:

- Region colours are **pale desaturated pastels**; Siberia's territories are near-white against a
  near-white icy ocean
- Territory names are **dark text on light**, the inverse of World Classic's white-on-dark
- **Region names are printed on the map** in coloured caps (NORTH AMERICA, SIBERIA, NUNAVUT,
  NORDIC, MOSKOVIA) — a distinct text class from territory names, and easy to mistake for one
- The decorative **arctic-circle ring is dashed**, visually identical to a sea route
- The bonus legend is a **circular** inset, not a rectangle

Other outliers in the set include Solar System (planets, not landmasses), Westeros, Tamriel, and
Jungle of Despair Classic (heaviest hatching at edge density 37).

**Conclusion: no single hardcoded heuristic generalises across this catalog.** A classical pipeline
tuned on World Classic will fail on Arctic Circle, and vice versa.

### Hybrid approach

Split the problem by what each technique is actually good at:

- **Segment Anything (SAM) for geometry.** Class-agnostic segmentation, so it is indifferent to
  palette, font, hatching and art style — precisely the axes on which this catalog varies. A
  colour-threshold pipeline tuned on World Classic would fail on Arctic Circle's near-white
  Siberia; SAM segments on structure instead. Contours from SAM masks give the territory polygons,
  centroids, and shared-border adjacency. Deterministic given a fixed model and fixed prompts.
- **Vision-model reading for semantics.** Which text is a territory name, a region name, a sea
  name, a title, or attribution; what the special rules say; what the bonus numerals are; which
  dashed lines are adjacency versus decoration. These are judgement calls that survive font,
  palette and style changes in a way thresholded heuristics do not.
- **Cross-validation between the two.** Geometry gives N polygons; the vision pass gives M named
  labels; disagreement between them is exactly the signal that a map needs human review, and feeds
  the confidence report.

## Honest limits

Fully automatic, accurate extraction across all 77 maps is not reliably achievable, and the design
says so rather than pretending otherwise:

- Dashed sea routes are **essential to correctness** and are the hardest element — they cross open
  water, are visually similar to decorative strokes, and their endpoints must be resolved to
  territories.
- OCR must separate territory names from sea names, titles and attribution, then reconcile
  abbreviations against canonical names we mostly do not have.
- Prose rules carry game semantics no parser will infer. They are captured **verbatim with their
  bounding box**, never parsed into machine-actionable rules.
- **Ground truth exists for exactly one map.** There is no automatic way to verify the other 76.

**Chosen posture (agreed 2026-08-10):** emit candidate geometry for all 77 plus a per-map
confidence report; flag maps failing checks for human review rather than shipping them silently.

**Every map is verified individually.** Each one has something unique — a circular legend, a
decorative dashed ring, planets instead of landmasses, region names printed as captions — so no
aggregate pass rate substitutes for looking at each map. The pipeline's deliverable is therefore a
**reviewable artifact per map**, not a trusted output: an overlay PNG showing segmented boundaries
and numbered centroids over the original artwork, next to the confidence report. A map is only
considered done once a human has looked at its overlay.

This makes review throughput, not raw segmentation accuracy, the thing the design must optimise:
the overlays must make an error obvious in a glance.

## The validation gate

`tests/fixtures/game_map1_territories.html` gives World Classic exactly: 42 territories, 83
undirected edges, per-territory `data-x`/`data-y`, and canonical names. Every image's dimensions
match its catalog `width`/`height`, so **label coordinates index directly into image pixel space
with no registration step.**

That yields a precise, non-negotiable test of territory segmentation:

> Each of the 42 known label coordinates must fall inside **exactly one** segmented territory, and
> that mapping must be a **bijection** — 42 points to 42 distinct polygons.

This checks segmentation without needing adjacency, and it fails loudly on both over-segmentation
(a territory split in two) and under-segmentation (two territories merged).

Adjacency is checked separately and asymmetrically: shared-border adjacency must be a **subset** of
the known 83 edges. Missing edges are expected — they are the sea routes. Any edge found that is
*not* in the 83 is a real error.

## Staging

Deliberately staged; each stage is independently verifiable.

| stage | delivers | gate |
|-|-|-|
| **1** | load + region clustering + territory segmentation + SVG outlines + confidence report | the 42-point bijection on World Classic |
| 2 | OCR: label text and positions; separate territory names from decoration | recover ≥90% of World Classic's 42 names |
| 3 | adjacency: shared borders, then dashed sea routes | subset of 83; then recover the ~10 sea routes |
| 4 | special rules: verbatim text blocks + bounding boxes; region bonus numerals | World Classic's 5/5/7/2/3/2 legend |

Stage 1 is the foundation and the subject of the first implementation pass.

## Output format

Per map, written under `data/processed/maps/<map_id>/`:

- `territories.svg` — one `<path>` per territory, `id` set to the territory index, region as a class
- `territories.json` — per territory: index, polygon, centroid, area, region id, OCR name (stage 2)
- `report.json` — confidence: segmented vs catalog territory/region counts, bijection result where
  ground truth exists, unmatched labels, warnings

`data/processed/` is gitignored. **Map artwork is D12's and is never republished** — only derived
geometry appears in figures.

## Non-goals

- Parsing prose rules into executable game logic
- Any network access — the images are already local
- Replacing D12 markup where we have it: for World Classic, markup adjacency and names remain
  authoritative and the segmentation is validated against them, not preferred over them
