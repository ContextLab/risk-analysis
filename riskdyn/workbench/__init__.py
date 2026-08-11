"""Per-map extraction workbench (issue #4).

Turns stage-1 segmentation plus per-map authored annotations into the
durable artifact set under ``data/processed/maps/<id>/``:

    territories.json  -- named territories with polygons (schema v2)
    territories.svg   -- derived from territories.json (regenerable)
    graph.json        -- adjacency with per-edge kind + provenance
    bonuses.json      -- bonus structures, incl. unresolved/prose states
    report.json       -- criteria (a)-(d), each pass | fail | unverified
    overlay.png       -- named overlay for human verification

All manual work lives in the committed ``data/authored/maps/<id>/
annotations.json``; everything under ``data/processed`` is derived and can
be regenerated with ``./.venv/bin/python -m riskdyn.workbench.build <id>``.
"""
