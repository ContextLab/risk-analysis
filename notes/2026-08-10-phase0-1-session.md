# Session notes — 2026-08-10: riskdyn phases 0–1

## What shipped

`riskdyn`, phases 0–1 of [#1](https://github.com/ContextLab/risk-analysis/issues/1): the
permission-gated fetch layer and the maps module. **115 tests**, all against real D12 data, no
mocks anywhere. 27 commits.

```
riskdyn/paths.py              canonicalize_path() — single source of truth for path comparison
riskdyn/config.py             Settings, PermissionRecord (sole mechanism that widens access)
riskdyn/sources/d12/          robots gate, rate limiter, cache, D12Client, 2 parsers
riskdyn/maps/                 model, adjacency graph + invariants, vector renderer
riskdyn/cli.py                pull-catalog, pull-images
```

Run tests: `./.venv/bin/python -m pytest tests/ -q` (115) or `-m "not network"` (110, offline).

## Where things stand with D12

**Permission request sent 2026-08-10, no reply yet.** Follow up **2026-08-24**. Everything built
so far uses only robots-allowed paths (`/maps`, `/api/`, `/assets/`, `/image/`). Phases 2–6 of the
spec are gated on the reply.

The ToS prohibits scraping without written permission and `robots.txt` disallows `/game/`,
`/user/`, `/userlist`. The code enforces this rather than relying on discipline: `D12Client` is the
only network module, the robots snapshot is active before the first request, a `PermissionRecord`
is the only thing that unlocks a disallowed path, `refresh_robots` can only make the policy
stricter, redirects raise instead of being followed, and the rate limiter is not optional.

## What we learned about D12's data

| thing | finding |
|-|-|
| map catalog | inline JSON on `/maps`, 77 maps, **1,232,133 games** total |
| map ids | **not contiguous** — 1–104 with 27 gaps; gaps still serve images but have no metadata |
| map images | `/assets/img/maps/<id>.large.jpg`, ~250 KB each, robots-allowed, no auth |
| topology | `/game/<id>` pages only — `data-territory`, `data-adjacencies`, `data-x/y`, `data-name` |
| **continents** | **absent from all markup** — the blocker behind [#4](https://github.com/ContextLab/risk-analysis/issues/4) |
| `/mappanel/map/<id>` | not a real route; 302s to `/auth/login` |
| chat reputation | `/userlist` carries a per-player Chat score — a free covariate for later |

World Classic ground truth (from Jeremy's saved page, scrubbed into
`tests/fixtures/game_map1_territories.html`): **42 territories, 83 undirected borders**, adjacency
symmetric, fully connected, degrees 2–6.

## Issues opened

- [#2](https://github.com/ContextLab/risk-analysis/issues/2) game simulator (needed for
  counterfactuals and null models, **not** for win probability — that's supervised on real outcomes)
- [#3](https://github.com/ContextLab/risk-analysis/issues/3) generalize to other games/corpora
- [#4](https://github.com/ContextLab/risk-analysis/issues/4) recover continents by segmenting map
  artwork; validate against the 83 known World Classic edges before trusting it on the other 76
- [#5](https://github.com/ContextLab/risk-analysis/issues/5) publication-quality label placement —
  the renderer works but Western/Eastern Australia overlap; **not paper-ready**

## The main lesson from this run

**Nine of eleven tasks needed a fix round, and essentially every finding was a gap in the plan I
wrote, not an implementer error.** The reference code in the plan was readable and looked correct;
readable is not the same as defensive. Canonicalization, input validation, write atomicity,
redirect handling, and cross-function consistency all had to be layered on afterwards.

The most instructive one: the robots gate took **three** hardening rounds for exotic bypasses
(`..` traversal, `%2F`, `//host/x`), and then the *final* whole-branch review found that plain
`/game/` was allowed — `normpath` strips the trailing slash, so `/game/` became `/game`, which
doesn't match the rule `/game/`. The obvious case survived three rounds of chasing clever ones.

Two implementer self-reports also claimed verification they hadn't done (an unused import
"removed" that was still there; "no overlapping text" on a render where two labels plainly
overlap). Worth continuing to check artifacts directly rather than trusting summaries.

## Deferred, triaged at final review — accepted, not fixed

- backslash paths (`/game\..\user`) not canonicalized — POSIX backend, not exploitable
- `parse_catalog` raises raw `JSONDecodeError` / `TypeError` / `KeyError` on malformed input
  rather than branded errors — fail-loud is acceptable
- `parse_topology` regex matches double-quoted attributes only — failure is loud, D12 emits doubles
- empty (not missing) `data-x` raises an uncaught `ValueError`
- `refresh_robots` has no automatic caller; `DEFAULT_ROBOTS` is a 2026-08-10 snapshot. **Whoever
  wires up a long crawl should call it periodically.**
- rate limiter is per-client; fine because `_pull_images` reuses one client across its whole loop

## Next

Phase 2 (parse + board-state reconstruction with the replay oracle) is the next milestone and is
**permission-gated**. Unblocked meanwhile: [#4](https://github.com/ContextLab/risk-analysis/issues/4),
which needs no new access and would restore continent bonuses to the metric design.
