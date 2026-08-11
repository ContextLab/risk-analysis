"""One-off schema migration (issue #4): scalar territory ``region_id`` ->
``region_ids`` list in every ``data/authored/maps/*/annotations.json``.

Territory-to-region is many-to-many (map 7's city bonuses overlap its
colour regions), so the scalar is replaced by a list: ``region_id: 3``
becomes ``region_ids: [3]`` and ``region_id: null`` becomes
``region_ids: []``.  The key keeps its position in each territory record
so the diff touches only the membership lines.  The importer-written
``notes`` prose describing the schema ("region_id is null") is updated to
match ("region_ids is []").

Refuses to guess: a territory carrying BOTH keys, or a non-scalar
``region_id``, aborts the run.

Run once from the repo root:

    ./.venv/bin/python scripts/convert_region_ids_to_lists.py
"""
from __future__ import annotations

import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
AUTHORED_ROOT = REPO_ROOT / "data" / "authored" / "maps"


def convert_territory(t: dict, path: pathlib.Path) -> tuple[dict, bool]:
    """One territory record; returns (converted record, changed?)."""
    if "region_id" not in t:
        if "region_ids" not in t:
            raise ValueError(
                f"{path}: territory {t.get('territory_id')} has neither "
                "region_id nor region_ids; not guessing"
            )
        return t, False
    if "region_ids" in t:
        raise ValueError(
            f"{path}: territory {t.get('territory_id')} carries both "
            "region_id and region_ids; resolve by hand"
        )
    rid = t["region_id"]
    if rid is not None and not isinstance(rid, int):
        raise ValueError(
            f"{path}: territory {t.get('territory_id')} has non-integer "
            f"region_id {rid!r}; not guessing"
        )
    out = {}
    for key, value in t.items():  # keep key order; swap in place
        if key == "region_id":
            out["region_ids"] = [] if rid is None else [rid]
        else:
            out[key] = value
    return out, True


def main() -> int:
    paths = sorted(
        AUTHORED_ROOT.glob("*/annotations.json"),
        key=lambda p: int(p.parent.name),
    )
    if not paths:
        print(f"no annotations found under {AUTHORED_ROOT}", file=sys.stderr)
        return 1
    n_files = n_terr = 0
    for path in paths:
        doc = json.loads(path.read_text())
        changed = False
        new_terr = []
        for t in doc.get("territories", []):
            rec, did = convert_territory(t, path)
            new_terr.append(rec)
            changed |= did
            n_terr += did
        stale = "region_id is null and regions is empty"
        if isinstance(doc.get("notes"), str) and stale in doc["notes"]:
            doc["notes"] = doc["notes"].replace(
                stale, "region_ids is [] and regions is empty"
            )
            changed = True
        if changed:
            doc["territories"] = new_terr
            path.write_text(json.dumps(doc, indent=1))
            n_files += 1
            print(f"converted {path.relative_to(REPO_ROOT)}")
    print(f"{n_files} file(s) converted, {n_terr} territory record(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
