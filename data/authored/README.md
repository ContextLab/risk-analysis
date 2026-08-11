# data/authored — committed manual work, per map

Everything under `data/authored/maps/<id>/annotations.json` is hand- or
vision-authored and **committed to git** (unlike `data/raw` and
`data/processed`, which are derived/downloaded and gitignored). This file is
the single home of all manual work for a map: corrections to segmentation,
region/bonus transcriptions, edge confirmations, and human verification
sign-offs. Everything in `data/processed/maps/<id>/` is regenerable from
the artwork + cached SAM masks + this file:

    ./.venv/bin/python -m riskdyn.workbench.build <map_id>

See `riskdyn/workbench/` docstrings for the schema, and
`docs/superpowers/specs/2026-08-11-map-extraction-plan-v3.md` for the
acceptance criteria the generated `report.json` scores.

Do not put secrets or player-identifying data here; this directory is
committed.
