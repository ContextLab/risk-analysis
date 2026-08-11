"""Import a manually saved D12 game page into schema-v2 annotations.

Jeremy saves D12 game pages in his browser, one per map, into
``data/raw/saved_pages/<map_id>.html`` (gitignored: saved pages carry live
CSRF/session tokens).  Each page contains D12's own territory markup::

    <a href="#5" id="territory-5" class="js_territory" data-territory="5"
       data-adjacencies="7,11,66,69" data-x="92" data-y="68"
       data-name="Northwest Territory">

This module turns such a page into ``data/authored/maps/<map_id>/
annotations.json`` (schema v2, the format ``riskdyn.workbench.graph_build``
consumes).  Parsing is delegated to ``riskdyn.sources.d12.parse_topology``
-- there is exactly one parser for this markup.

What the markup can and cannot provide:

- territories: id, name, x, y  -> ``source: "d12-markup"``, high confidence
- edges: every undirected pair from ``data-adjacencies``.  Existence is
  certain (it is D12's own data) so ``status: "confirmed"``; the
  border-vs-route classification is NOT in the markup, so ``kind:
  "unknown"`` with ``kind_status: "unconfirmed"``.
- continent membership is ABSENT from D12's markup (issue #4): every
  ``region_id`` is ``null`` and ``regions`` starts empty.  Regions are
  authored later from the artwork; this importer never invents them.

Overwriting: an existing ``annotations.json`` is never touched without
``--force``.  With ``--force`` the hand-authored blocks -- ``regions``,
``extra_bonuses``, ``special_rules``, ``verification``, plus
``edge_confirmations``, ``corrections`` and ``notes`` -- are preserved
verbatim (the markup cannot replace that work) while ``territories`` and
``edges`` are rebuilt from the page.  If the old territories carried
region assignments, a loud warning notes they must be re-authored (the
preserved ``regions`` block retains its ``territory_names`` for recovery).

Security: saved pages hold live session tokens.  The written annotations
contain ONLY territory ids, names, coordinates and adjacency -- never raw
HTML, tokens, cookies, usernames, or game/player state.  The document is
built exclusively from the typed fields ``parse_topology`` extracts, and
territory names are rejected if they contain markup characters.

CLI::

    ./.venv/bin/python -m riskdyn.workbench.import_page <map_id> [--html PATH] [--force]
    ./.venv/bin/python -m riskdyn.workbench.import_page --status
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import pathlib
import sys

from riskdyn.segment import catalog as cat
from riskdyn.sources.d12.parse_topology import parse_topology
from riskdyn.workbench.build import AUTHORED_ROOT

SAVED_PAGES_ROOT = cat.REPO_ROOT / "data" / "raw" / "saved_pages"

# Hand-authored blocks a --force re-import must carry over verbatim: the
# markup cannot reproduce this work, and a careless overwrite destroys it.
PRESERVED_KEYS = (
    "regions",
    "extra_bonuses",
    "special_rules",
    "verification",
    "edge_confirmations",
    "corrections",
    "notes",
)


@dataclasses.dataclass
class ImportResult:
    map_id: int
    path: pathlib.Path
    doc: dict
    warnings: list[str]


def _fresh_verification() -> dict:
    # Same shape as data/authored/maps/1/annotations.json: nothing verified.
    return {
        "overlay_confirmed": {"verified": False, "by": None, "at": None},
        "bonuses_confirmed": {"verified": False, "by": None, "at": None},
    }


def _territory_records(topo) -> list[dict]:
    records = []
    for t in sorted(topo.territories, key=lambda t: t.territory_id):
        if "<" in t.name or ">" in t.name:
            raise ValueError(
                f"territory {t.territory_id} name {t.name!r} contains markup "
                "characters; refusing to write it into annotations"
            )
        records.append(
            {
                "territory_id": t.territory_id,
                "name": t.name,
                "x": t.x,
                "y": t.y,
                # Continent membership is not in D12's markup (issue #4).
                "region_id": None,
                "source": "d12-markup",
                "confidence": "high",
            }
        )
    return records


def _edge_records(topo) -> tuple[list[dict], list[str]]:
    """Every undirected pair from data-adjacencies, canonicalized a < b."""
    warnings: list[str] = []
    ids = {t.territory_id for t in topo.territories}
    directed = {
        (t.territory_id, adj) for t in topo.territories for adj in t.adjacencies
    }
    pairs: set[tuple[int, int]] = set()
    for a, b in directed:
        if b not in ids:
            raise ValueError(
                f"territory {a} lists adjacency to unknown territory id {b}; "
                "the saved page is inconsistent -- not repairing it"
            )
        if a == b:
            raise ValueError(f"territory {a} lists itself as an adjacency")
        pairs.add((min(a, b), max(a, b)))
    for a, b in sorted(pairs):
        if (a, b) not in directed or (b, a) not in directed:
            warnings.append(
                f"adjacency {a}-{b} appears in only one direction in the "
                "markup; edge kept (existence is D12's claim)"
            )
    edges = [
        {
            "a": a,
            "b": b,
            "kind": "unknown",
            "kind_status": "unconfirmed",
            "wraps": False,
            "rule": None,
            "status": "confirmed",
            "source": "d12-markup",
            "note": "",
        }
        for a, b in sorted(pairs)
    ]
    return edges, warnings


def annotations_from_page(html: str, map_id: int) -> tuple[dict, list[str]]:
    """Schema-v2 annotations document from saved-page markup."""
    topo = parse_topology(html, map_id)
    territories = _territory_records(topo)
    edges, warnings = _edge_records(topo)
    doc = {
        "schema_version": 2,
        "map_id": map_id,
        "notes": (
            "Imported from a saved D12 game page by "
            "riskdyn.workbench.import_page on "
            f"{datetime.date.today().isoformat()}. Territories and the edge "
            "LIST come from D12's own territory markup and are authoritative "
            "-- edge status 'confirmed' asserts EXISTENCE only; kind stays "
            "'unknown'/'unconfirmed' until classified. Continent membership "
            "is absent from D12's markup (issue #4): region_id is null and "
            "regions is empty until authored from the artwork."
        ),
        "territories": territories,
        "edges": edges,
        "edge_confirmations": [],
        "special_rules": [],
        "corrections": [],
        "regions": [],
        "extra_bonuses": [],
        "verification": _fresh_verification(),
    }
    return doc, warnings


def _catalog_warnings(map_id: int, n_territories: int) -> list[str]:
    warnings: list[str] = []
    catalog = cat.load_catalog()
    summary = catalog.get(map_id)
    if summary is None:
        warnings.append(
            f"map {map_id} is not in the catalog "
            f"({cat.CATALOG_JSON.name}); territory count cannot be "
            "cross-checked -- importing anyway"
        )
    elif summary.num_territories != n_territories:
        warnings.append(
            f"TERRITORY COUNT MISMATCH for map {map_id} "
            f"({summary.name}): page has {n_territories} territories but the "
            f"catalog expects {summary.num_territories} -- writing anyway; "
            "verify the saved page is the right map"
        )
    return warnings


def import_page(
    map_id: int,
    html_path: pathlib.Path | None = None,
    force: bool = False,
    authored_root: pathlib.Path | None = None,
) -> ImportResult:
    """Import one saved page; returns the written document plus warnings.

    Raises FileNotFoundError if the saved page is missing and
    FileExistsError if annotations exist and ``force`` is not set.
    """
    html_path = html_path or SAVED_PAGES_ROOT / f"{map_id}.html"
    if not html_path.is_file():
        raise FileNotFoundError(
            f"{html_path} not found: save the D12 game page for map "
            f"{map_id} there (or pass --html)"
        )
    out_path = (authored_root or AUTHORED_ROOT) / str(map_id) / "annotations.json"

    doc, warnings = annotations_from_page(html_path.read_text(), map_id)
    warnings += _catalog_warnings(map_id, len(doc["territories"]))

    if out_path.is_file():
        if not force:
            raise FileExistsError(
                f"{out_path} already exists; refusing to overwrite without "
                "--force (it may contain hand-authored work)"
            )
        existing = json.loads(out_path.read_text())
        for key in PRESERVED_KEYS:
            if key in existing:
                doc[key] = existing[key]
        assigned = [
            t["territory_id"]
            for t in existing.get("territories", [])
            if t.get("region_id") is not None
        ]
        if assigned:
            warnings.append(
                f"overwrote territories that carried region assignments for "
                f"{len(assigned)} territory(ies) {assigned}; region_id is "
                "not in D12's markup, so re-author region membership (the "
                "preserved 'regions' block keeps its territory_names)"
            )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, indent=1))
    return ImportResult(map_id=map_id, path=out_path, doc=doc, warnings=warnings)


# --------------------------------------------------------------------------
# coverage report
# --------------------------------------------------------------------------

def status_report(
    authored_root: pathlib.Path | None = None,
    saved_root: pathlib.Path | None = None,
) -> str:
    authored_root = authored_root or AUTHORED_ROOT
    saved_root = saved_root or SAVED_PAGES_ROOT
    catalog = cat.load_catalog()

    def has_page(mid: int) -> bool:
        return (saved_root / f"{mid}.html").is_file()

    def has_annotations(mid: int) -> bool:
        return (authored_root / str(mid) / "annotations.json").is_file()

    imported, page_only, neither = [], [], []
    for mid in sorted(catalog):
        if has_annotations(mid):
            imported.append(mid)
        elif has_page(mid):
            page_only.append(mid)
        else:
            neither.append(mid)

    # glob on a missing directory simply yields nothing
    extra_ids = sorted(
        {
            int(p.stem)
            for p in saved_root.glob("*.html")
            if p.stem.isdigit() and int(p.stem) not in catalog
        }
        | {
            int(d.name)
            for d in authored_root.glob("*")
            if d.name.isdigit()
            and int(d.name) not in catalog
            and (d / "annotations.json").is_file()
        }
    )

    def fmt(mid: int) -> str:
        name = catalog[mid].name if mid in catalog else "(not in catalog)"
        marks = []
        if has_page(mid):
            marks.append("saved page")
        if has_annotations(mid):
            marks.append("annotations")
        return f"  map {mid:>4}  {name}  [{', '.join(marks) or 'nothing'}]"

    lines = [f"catalog maps: {len(catalog)}"]
    lines.append(f"imported (annotations.json exists): {len(imported)}")
    lines += [fmt(m) for m in imported]
    lines.append(f"saved page but not yet imported: {len(page_only)}")
    lines += [fmt(m) for m in page_only]
    lines.append(f"neither saved page nor annotations: {len(neither)}")
    lines += [fmt(m) for m in neither]
    if extra_ids:
        lines.append(f"not in catalog (still importable): {len(extra_ids)}")
        lines += [fmt(m) for m in extra_ids]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="riskdyn.workbench.import_page",
        description="saved D12 game page -> schema-v2 annotations.json",
    )
    ap.add_argument("map_id", nargs="?", type=int, help="D12 map id")
    ap.add_argument(
        "--html",
        type=pathlib.Path,
        help="saved page path (default data/raw/saved_pages/<map_id>.html)",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="overwrite existing annotations.json (hand-authored regions/"
        "bonuses/rules/verification blocks are preserved)",
    )
    ap.add_argument(
        "--status",
        action="store_true",
        help="report which catalog maps have a saved page and/or annotations",
    )
    ap.add_argument(
        "--authored-root",
        type=pathlib.Path,
        help="override data/authored/maps (tests, alternate layouts)",
    )
    ap.add_argument(
        "--saved-root",
        type=pathlib.Path,
        help="override data/raw/saved_pages for --status",
    )
    args = ap.parse_args(argv)

    if args.status:
        print(status_report(args.authored_root, args.saved_root))
        return 0
    if args.map_id is None:
        ap.error("map_id is required unless --status is given")

    try:
        result = import_page(
            args.map_id,
            html_path=args.html,
            force=args.force,
            authored_root=args.authored_root,
        )
    except (FileNotFoundError, FileExistsError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    for w in result.warnings:
        print(f"WARNING: {w}", file=sys.stderr)
    print(
        f"map {result.map_id}: wrote {result.path} "
        f"({len(result.doc['territories'])} territories, "
        f"{len(result.doc['edges'])} edges, all region_id null -- regions "
        "must be authored from the artwork)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
