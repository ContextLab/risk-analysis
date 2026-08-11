# Session notes: review UI for human sign-off (issue #4)

Date: 2026-08-11

## What was built

`riskdyn/workbench/review.py` — a stdlib-only local web UI
(`./.venv/bin/python -m riskdyn.workbench.review [--port 8765]
[--by "Jeremy Manning"] [--open]`), bound to 127.0.0.1 only.

- `/` index: all 78 maps, priority-sorted (open conflicts -> unverified
  bonuses with a legend read -> rest), sortable columns, progress line,
  link to the first map needing attention.
- `/map/<id>`: (1) bonus rows with 3x nearest-neighbour `bonus_bbox`
  crops + whole-legend context crop, confirm/wrong-with-value per region;
  (2) conflict rows from `region_conflicts.json` with 240px territory
  crops centred on the label-box CENTRE `(x+15, y+10)`, swatches, singleton
  badges, legend/colour/neither rulings; (3) overlay.png +
  region_overlay.png with looks-right/problem.
- Keyboard: j/k/y/n/e/Enter/?; auto-advance; everything also clickable.
- Decisions POST to `/api/decision` and are written atomically
  (tempfile + os.replace, indent=1) into
  `data/authored/maps/<id>/annotations.json` under `verification`.
- Bonus sign-off: per-region confirms accumulate in
  `bonuses_confirmed.region_ids_confirmed`; corrections append to
  `corrections` AND update the v3 legend file (validated before write).
  When every region is decided, `verified: true` + `by` + `at` +
  `payload_sha256` are recorded.
- `payload_sha256` = sha256 of canonical
  {regions: [{region_id, bonus, bonus_text_verbatim}], extra_bonuses,
  special_rules}. Helpers `bonus_payload` / `payload_sha256` /
  `bonus_payload_sha256` live in `graph_build.py`.
- `graph_build._crit_d` now recomputes that hash: sign-off with a
  mismatching (or missing) hash is STALE -> criterion d falls back to
  unverified, report carries `stale_signoff: true`, UI shows "data
  changed since sign-off".
- Unmerged legend maps (7, 25, 100) display bonus rows from the legend
  file; the sign-off hash covers those values, and it survives the
  eventual merge because merge_legend writes the same value/verbatim
  triples into annotations.
- Refuses to record without `--by`, and refuses agent identities
  (via `_human_signoff`).

## Tests

`tests/test_review.py` — 14 tests, real server on a real socket, real
data, no mocks; decision tests run on copies under tmp_path. Full suite:
281 passed, 5 network-deselected (was 267 before this task).

## State of the data (unchanged by this session)

- 78 maps authored; legends exist for 1, 7, 25, 100 (25 has 0 regions).
- Map 1 has merged regions in annotations; 7/100 merges refused
  (4 / 23 conflicts, map 100 has 4 colour-isolated singletons).
- No sign-off recorded anywhere yet; criterion d unverified on all 78.

## Loose ends / next steps

- Jeremy to actually run the UI and sign off.
- `code/scripts/` untracked dir existed before this session — not touched.
- Adjudications are recorded in `verification.conflicts_adjudicated` but
  nothing yet consumes them to re-drive merge_legend; that is the natural
  next task.
