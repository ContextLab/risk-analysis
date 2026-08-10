# Conversation and position dynamics in online Risk — design

**Date:** 2026-08-10
**Issue:** [#1](https://github.com/ContextLab/risk-analysis/issues/1)
**Status:** design approved, pending spec review
**Deferred to:** [#2](https://github.com/ContextLab/risk-analysis/issues/2) (simulator), [#3](https://github.com/ContextLab/risk-analysis/issues/3) (other games/corpora)

## Goal

Build a Python package for analyzing games played on [Dominating 12](https://dominating12.com),
an online Risk implementation, and write a paper reporting results. The scientific interest is
conversation dynamics: what players say, as a function of where they stand on the board.

The lab's focus is conversation/event/thought dynamics, so the conversation layer is central,
not peripheral. Board-state modeling exists to make "where they stand" a computable quantity.

## Data access and permission status

**This is a live constraint on the project, not a footnote.**

D12's Terms of Use (`/legal/terms`, rev. 2009-12-05) state that no Site Content may be
"scraped … without the Company's prior written permission," and that the granted license "does
not permit use of any data mining, robots, scraping or similar data gathering or extraction
methods." User Conduct separately prohibits "automated scripts to collect information from …
the Site." `robots.txt` disallows `/game/`, `/user/`, and `/userlist` — and nothing else.

A permission request was **sent 2026-08-10** via https://dominating12.com/contact; text and
status are tracked in `notes/d12-permission-request.md`. Written permission is the remedy the ToS
itself names. Phases 2+ below are gated on the reply.

Two things are on firmer footing regardless:

- **Own User Content.** The ToS exempts "your own User Content." Games `jrm` played in fall
  under this; other players' messages within those games do not.
- **Facts are not copyrightable.** Territory adjacency derived from a map is factual data. Their
  map *artwork* is trademarked and will not be republished; figures use our own rendering of
  derived structure.

### Requirements this places on the code

1. `fetch` refuses `robots.txt`-disallowed paths unless an explicit permission record is
   configured, naming what was granted (paths, rate, retention, publication terms).
2. Rate limiting is on by default and configurable to whatever D12 specifies.
3. Everything is cached on first fetch. Re-analysis never re-crawls.
4. Identifying `User-Agent` with contact address.
5. Usernames pseudonymized at the publication boundary — real usernames may exist in
   `data/raw`, but anything reaching `paper/` is mapped through a pseudonym table that is
   itself gitignored.

## Site reconnaissance

Read from `bundle.js`. D12 exposes a JSON API, so most retrieval is not HTML scraping:

| endpoint | purpose | robots |
|-|-|-|
| `/api/user/names?q=%QUERY` | username → user record | allowed |
| `/api/game/<id>` | game lookup | allowed |
| `/game/<id>/play/update-state` | live poll: board + log + chat | disallowed |
| `/chat`, `/chat/<id>` | conversation threads | not listed |
| `/game/<id>/debug/{state,events,stack}` | full state/event dump | `/modpanel/`, mod-only |

Relevant details:

- Users are keyed by **numeric ID** (`/user/55893`), not username. The twelve target usernames
  require a resolution hop through `/api/user/names`.
- The live-game protocol is **incremental** (`last_update` cursor, `addLogEntry`,
  `#game-log-list`), so log entries and chat arrive structured and already interleaved. Issue #1
  step 3 ("match up logs and conversations") is therefore expected to be cheap, not a hard
  alignment problem.
- `/userlist` carries a per-player **Chat reputation score** alongside fair-play and attendance —
  a ready-made external covariate.
- The `debug/*` endpoints would replace all page-level retrieval with one call per game. **Ask
  for access to these explicitly in the permission thread.**

### Target players

A seed set of twelve usernames, plus the top-100 list. **The seed list is deliberately not in this
document.** It lives in `config/target_players.txt`, which is gitignored.

These are identifiable people. Their usernames are public on D12's leaderboard, but publishing
"these twelve are subjects of a study" in a public repository is a new disclosure that the
leaderboard does not make, and it costs nothing to avoid. The same reasoning applies to the
pseudonym table (`config/pseudonyms.csv`, also gitignored), which is the only artifact that can
re-identify players in published figures.

## Scope

**In scope (v1):** analysis of real D12 data. Retrieval, parsing, map→graph extraction, vector
map rendering, board-state reconstruction, position-strength metrics including a learned win
probability, conversation embedding and alignment, the four analyses from issue #1, playback
tooling, and a paper.

**Out of scope (v1):** the game simulator (#2) and other games/corpora (#3). The simulator is
needed eventually — see "Position strength" — but is not on the critical path.

## Architecture

Site-specific code is confined to an adapter, so #3 is additive rather than a rewrite.

```
riskdyn/
  sources/          site-specific adapters — the only site-aware code
    d12/            auth.py, fetch.py, parse.py, cache.py
  maps/             map definition -> adjacency graph + vector rendering
  game/             event log -> per-turn board state sequence      <-- linchpin
  metrics/          position strength (player x turn), graph metrics (per map)
  text/             conversation turns -> embeddings, aligned to board state
  analysis/         clustering, trajectory models, statistics
  viz/              static figures + animated playback
  sim/              interface only in v1; implementation in #2
```

Layering rules:

- **Only `sources/*/fetch` touches the network.** Everything downstream runs offline.
- **`parse` is pure.** Raw payload in, typed records out. Tested against checked-in fixtures.
- Package name `riskdyn` is confirmed.

## Data model

Four types; everything else is derived.

- **`Map`** — territories, adjacency, continents and bonus values, 2-D geometry for rendering
- **`Game`** — map, players, ordered events, outcome
- **`BoardState`** — ownership and troop count per territory at a point in time
- **`Message`** — game, sender, timestamp, text, **and the index of the `BoardState` it was sent
  under**

That last field is what makes "what people say as a function of their current position strength"
computable rather than rhetorical. It is the join key for the entire paper.

## Board-state reconstruction

`game/` replays the event log to produce a `BoardState` after every event. Position strength,
message alignment, and playback all depend on it.

**Correctness oracle:** replaying a completed game must reproduce its reported final state and
winner. Mismatch means the rules model is wrong, and fails loudly on a specific game and event.
This runs across every game in the corpus, uses only data we already have, requires no mocks or
hand-labeled ground truth, and strengthens as the corpus grows. It is the primary correctness
guarantee for the package.

## Position strength

The metric we want is **win probability from position**, calibrated to the actual D12 player
population — not to optimal play.

### Tier 1 — supervised value model (primary; no simulator)

Position features → P(this player eventually wins), trained on real games with real outcomes.

Calibration to the real player population is automatic: the labels come from games these people
actually played. A rollout estimate under optimal policies would answer a different question.

Feature families, which double as interpretable baselines:

| family | captures | misses |
|-|-|-|
| troop / territory counts | raw material | shape, position |
| bonus-weighted income | economic engine | vulnerability |
| border exposure (border:interior ratio, weighted by adjacent enemy troops) | defensibility | momentum |
| graph metrics of owned subgraph | structure | material |

Methodological requirements:

- **Grouped cross-validation by game.** Positions within a game share a label and are heavily
  autocorrelated; ignoring this leaks and inflates accuracy.
- **Calibration matters, not just ranking.** Report reliability curves — downstream analyses use
  the probability as a regressor, so miscalibration propagates.
- Report the increment from topology features over material-only, which is the substantive
  question: does "position" mean *resources* or *structure*?

### Tier 2 — simulator and rollouts (issue #2)

Buys what Tier 1 cannot: **counterfactuals** (replay from turn *k* with a different move, measure
the swing in win probability), null models for the clustering claims, coverage of rare
configurations, and an independent check on Tier 1.

**Depth-first search is intractable and is not the approach.** Reinforcement placement alone is
`C(a+t-1, t-1)` — 1,961,256 placements for 10 armies over 15 territories, 2.5×10¹⁰ for 15 over 25 —
at a single ply, before any attack is chosen. Three independent blockers, each fatal alone:
chance nodes at every attack (alpha-beta requires deterministic minimax; `*`-minimax prunes far
less); n > 2 players (alpha-beta is invalid for max^n, and the paranoid assumption distorts
exactly the coalition behavior of interest); and hidden card holdings. Chess has b≈35; Risk is
10⁶–10¹⁰ at one ply over 50–200 turns.

**Monte Carlo rollouts** with policies calibrated to observed human play. One exact optimization:
a Risk battle is an absorbing Markov chain, so no inner dice loop is needed. Single-throw
distributions computed by full enumeration (3v2 → 0.3717 / 0.3358 / 0.2926, matching published
Risk odds) feed a precomputed absorbing distribution per (attackers, defenders) pair; each attack
becomes one table lookup and one draw. Exact, not approximate.

### Tier 3 — truncated rollouts with the Tier-1 model as horizon evaluator

Only if needed.

## Conversation pipeline

`text/` embeds each conversation turn with **EmbeddingGemma**, behind an interface so the model is
swappable in one line. Turns carry their `BoardState` index, so every message has position
strength, game phase, and the positions of *other* players attached.

Analyses this enables, per issue #1 step 6:

1. **Per-map game dynamics** — do maps cluster? do games cluster? general trends?
2. **Per-player dynamics** — do players cluster?
3. **Per-game conversation dynamics** — do embedded conversation trajectories follow stereotyped
   arcs? Are there patterns in what people say as a function of own position strength, game phase,
   and others' positions?
4. **Graph metrics per map** as predictors of the above.

## Playback tooling

`viz/` renders animations of game and conversation dynamics together: board state over time
alongside the conversation trajectory. Also produces the static figures for the paper.

## Paper

**Spine deliberately undecided.** Per the approved plan, we run all four analyses
descriptively on real data first and choose the thesis from what is actually there. Candidate
spines considered and held open: conversation tracks position; talk predicts action; topology
shapes discourse.

**This is an explicit decision point**, not an omission. Revisit after the first descriptive pass.

Figures are generated by notebooks in `code/notebooks/`, written as vector panels to
`paper/figs/source/`, and composed per the repo's existing linked-PDF convention (see `CLAUDE.md`).

## Testing

Per the repo's standing testing policy: real calls, real data, no mocks and no mock fallbacks.

- `parse` tested against checked-in fixtures captured from real responses
- `game` tested by the replay oracle across the whole corpus
- `maps` tested by rendering every map and verifying graph invariants (connectivity, symmetry of
  adjacency, continent membership partitions the territory set)
- battle Markov chain validated against exact enumeration
- Tier-1 model validated by grouped CV and calibration curves
- embedding pipeline tested by an actual EmbeddingGemma call on real text
- figures exported to PNG and visually inspected

## Repo layout

```
riskdyn/              the package
code/notebooks/       one notebook per figure (existing convention)
data/raw/             cached API payloads (gitignored; may contain usernames)
data/processed/       parsed + derived tables (parquet)
paper/                LaTeX (existing convention)
notes/                session notes, permission correspondence
docs/superpowers/specs/   this document
```

## Implementation staging

This spec is deliberately larger than one implementation plan. It describes a coherent system, but
building it is phased, and each phase is independently useful and independently verifiable. The
implementation plan should follow these boundaries rather than attempting the whole thing at once.

| phase | delivers | verified by | gated on permission? |
|-|-|-|-|
| 0 | `sources/d12` fetch + cache + permission gate; resolve the 12 usernames via `/api/user/names` | real calls to robots-allowed endpoints | no |
| 1 | `maps`: map → adjacency graph + vector rendering | graph invariants; every map renders | no |
| 2 | `parse` + `game`: event log → `BoardState` sequence | **replay oracle** across the corpus | yes |
| 3 | `metrics`: feature families + Tier-1 win-probability model | grouped CV, calibration curves | yes |
| 4 | `text`: embedding + board-state alignment | real EmbeddingGemma call; join integrity | yes |
| 5 | `analysis` + `viz`: the four analyses, playback, figures | figures exported and inspected | yes |
| 6 | paper: pick the spine, write it | — | yes |

Phases 0 and 1 are unblocked today: `/api/user/names` and `/maps` are not robots-disallowed, and
map topology is factual data. Everything from phase 2 on needs the permission question resolved,
which is why the letter is the long pole and was drafted first.

## Open questions

1. **Corpus size** — unknown until the twelve usernames resolve and game counts are visible.
   Determines whether per-player clustering is powered.
2. **Chat visibility** — whether completed-game chat is retrievable in bulk, or only via the
   incremental live protocol. Affects retrieval design; resolve with an authenticated session.
3. **Permission outcome** — sent 2026-08-10; awaiting reply. The scope of what D12 grants
   determines how much of the corpus is reachable. The simulator (#2) is the fallback if the
   answer is restrictive. Follow up 2026-08-24 if no response.
