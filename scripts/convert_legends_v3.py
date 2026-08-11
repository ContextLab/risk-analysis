"""One-time migration: pilot legend drafts -> schema v3 legend files.

    ./.venv/bin/python scripts/convert_legends_v3.py --src <dir-with-pilot-files>

Reads the three pilot draft files (``legend-map1.json``, ``legend-map7.json``,
``legend-map100.json`` -- the pre-v3 form with scalar confidence, ``name:
null`` and bare-string ``territory_names``) and writes schema-v3 files to
``data/authored/maps/<id>/legend-map<id>.json``.  Also generates
``legend-map25.json`` from D12's own saved markup (map 25 has no pilot
legend read; only its ``map_specific`` unit-attack structure is derivable
mechanically).

The conversion applies the four membership corrections confirmed by the
adversarial red-team review of the pilot (legend-redteam.md, D1-D4):

    Chernigov   Rus (r3)        -> Khazars (r2)
    Kerch       Khazars (r2)    -> r4
    Edessa      Byzantine (r14) -> Saminid (r16)
    Alamania    r7              -> r8

and, per the same review, does NOT propagate pilot notes it proved false
(D6: the claimed colour-sampling disagreements for Tiflis/Derbent/Tabriz/
Baghdad never existed; D7: the "anchors land on open water" list was
mostly wrong).  Everything else is carried over verbatim.

Map 25's three unit classes are derived from the directed adjacency graph
by BFS (bidirectional edge => same class; one-way A->B => class(B) =
class(A)+1 mod 3) and recorded as class_0/1/2 -- NOT as cavalry/infantry/
artillery, because binding classes to the painted unit icons needs a full
icon read and only a partial spot-check exists.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import defaultdict, deque

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from riskdyn.sources.d12.parse_topology import parse_topology  # noqa: E402
from riskdyn.workbench.legend_schema import validate_legend_v3  # noqa: E402

OUT_ROOT = REPO_ROOT / "data" / "authored" / "maps"

# red-team D1-D4: territory -> (from_region, to_region)
MAP100_CORRECTIONS = {
    "Chernigov": (3, 2),
    "Kerch": (2, 4),
    "Edessa": (14, 16),
    "Alamania": (7, 8),
}
CORRECTION_NOTE = {
    "Chernigov": "moved from Rus (r3) per red-team D1: salmon fill, thick "
    "region border against Kiev/Smolensk, colour cluster agrees (pc 1.00)",
    "Kerch": "moved from Khazars (r2) per red-team D2: yellow-green fill "
    "with Tmu Tarkan/Kuban, colour cluster agrees (pc 1.00)",
    "Edessa": "moved from Byzantine (r14) per red-team D3: sage-green fill "
    "contiguous with Mardin/Jazira; changes this territory's bonus 4 -> 3",
    "Alamania": "moved from r7 per red-team D4: bright pink fill east of "
    "the thick border with Saxon/Thuringia; changes bonus 2 -> 3",
}

# red-team D6: these four pilot member notes claimed a colour-sampling
# disagreement that never existed; replace with what was actually measured
D6_NOTE = (
    "assignment matches both the printed label and the colour cluster; the "
    "pilot's note claiming a colour-sampling disagreement here was false "
    "(red-team D6: cluster agrees, patch consistency 1.00)"
)
MAP100_MEMBER_NOTES = {
    "Tiflis": D6_NOTE,
    "Derbent": D6_NOTE,
    "Tabriz": D6_NOTE,
    "Baghdad": D6_NOTE,
}

MAP1_OCEAN_LABELS = {
    "Alaska", "Iceland", "Britain", "Scandinavia", "Japan", "Indonesia",
    "Papua New Guinea", "Western Australia",
}


def _name_obj(text, provenance, confidence, note="", spans=None) -> dict:
    return {
        "text": text,
        "provenance": provenance,
        "spans_region_ids": spans,
        "confidence": confidence,
        "note": note,
    }


def _member(name, confidence, evidence, note="") -> dict:
    return {
        "name": name,
        "confidence": confidence,
        "evidence": evidence,
        "colour_cluster": None,
        "agrees_with_cluster": None,
        "note": note,
    }


def _bonus(old: dict, confidence: str = "high") -> dict:
    return {
        "value": old["bonus"],
        "text_verbatim": old["bonus_text_verbatim"],
        "bbox": old["bonus_bbox"],
        "confidence": confidence,
    }


def _association(old: dict, confidence: str, note: str = "") -> dict:
    return {"method": old["association"], "confidence": confidence, "note": note}


def _unresolved_from(uncertainties: list[str]) -> list[dict]:
    return [{"what": u, "options": [], "why": ""} for u in uncertainties]


def _base_doc(old: dict) -> dict:
    return {
        "schema_version": 3,
        "map_id": old["map_id"],
        "legend_bboxes": old["legend_bboxes"],
        "regions": [],
        "extra_bonuses": [],
        "special_rules": [],
        "territories_in_no_region": [],
        "map_specific": {},
        "unresolved": [],
    }


# --------------------------------------------------------------------------
# map 1
# --------------------------------------------------------------------------

def convert_map1(old: dict) -> dict:
    doc = _base_doc(old)
    for r in old["regions"]:
        members = []
        for n in r["territory_names"]:
            conf, note = "high", ""
            if n == "Ukraine":
                conf = "medium"
                note = (
                    "rendered in a noticeably lighter slate-blue (#59678c) "
                    "than the rest of the blue group (#305492); grouped blue "
                    "because the legend shows only six numerals and the tint "
                    "sits inside the same black continental outline"
                )
            elif n in MAP1_OCEAN_LABELS:
                note = (
                    "label sits over ocean; fill read from the adjacent "
                    "landmass and confirmed visually at full-map zoom"
                )
            members.append(_member(n, conf, "colour-match", note))
        doc["regions"].append(
            {
                "region_id": r["region_id"],
                "name": _name_obj(
                    None, "none-printed", "high",
                    "No region name is printed anywhere on this map, "
                    "neither on the main map nor in the legend.",
                ),
                "kind": r["kind"],
                "layer": "base",
                "bonus": _bonus(r),
                "association": _association(r, "high", r["note"]),
                "colour_hex": r["colour_hex"],
                "members": members,
            }
        )
    doc["unresolved"] = _unresolved_from(old["uncertainties"])
    return doc


# --------------------------------------------------------------------------
# map 7
# --------------------------------------------------------------------------

def convert_map7(old: dict) -> dict:
    doc = _base_doc(old)
    for r in old["regions"]:
        overlay = r["kind"] == "bonus-group"
        members = []
        for n in r["territory_names"]:
            conf, note = "high", ""
            if n in ("Bull Mountain", "King City"):
                note = (
                    "the pilot task brief listed this territory as "
                    "region-less; the artwork read and the colour cluster "
                    "both put it in the green region (red-team confirmed: "
                    "cluster 1, label-on-parchment convention shared with "
                    "Metzger/Bridgeport/Fox Hill/the Sherwoods) -- agent "
                    "reads only, no human sign-off"
                )
            members.append(
                _member(n, conf, "legend-list" if overlay else "colour-match", note)
            )
        if overlay:
            name = _name_obj(
                r["name"], "inferred", "high",
                "The group name is NOT printed in the legend; it is the "
                "shared stem of the two numbered territory names the white "
                "outline encloses.",
            )
        else:
            name = _name_obj(
                None, "none-printed", "high",
                "None of the six colour regions carries a printed name; "
                "only the two legend panel titles ('Region Bonuses', "
                "'Additional City Bonuses') are printed.",
            )
        doc["regions"].append(
            {
                "region_id": r["region_id"],
                "name": name,
                "kind": r["kind"],
                "layer": "overlay" if overlay else "base",
                "bonus": _bonus(r),
                "association": _association(r, "high", r["note"]),
                "colour_hex": r["colour_hex"],
                "members": members,
            }
        )
    doc["territories_in_no_region"] = [
        {
            "name": n,
            "confidence": "high",
            "evidence": "neutral grey fill; the only three grey squares on "
            "the map (red-team verified crops)",
        }
        for n in old["territories_in_no_region"]
    ]
    doc["unresolved"] = _unresolved_from(old["uncertainties"])
    return doc


# --------------------------------------------------------------------------
# map 100
# --------------------------------------------------------------------------

MAP100_NONE_PRINTED = {1, 4, 5, 12, 22}
MAP100_RF_SPAN = [6, 7, 8, 9]
MAP100_BYZ_SPAN = [13, 14]
MAP100_ISLAND_SAMPLES = {
    "Baleares": "grey island fill; the sample point lands on sea "
    "(patch consistency 0.07), membership read visually",
    "Bari": "grey island fill; the sample point lands on sea "
    "(patch consistency 0.74), membership read visually",
    "Crete": "grey island fill; the sample point lands on sea "
    "(patch consistency 0.69), membership read visually",
    "Cyprus": "grey island fill; sample genuinely ambiguous "
    "(patch consistency 0.22), membership read visually",
}


def convert_map100(old: dict) -> dict:
    doc = _base_doc(old)
    # corrected membership: region_id -> list of names
    membership: dict[int, list[str]] = {
        r["region_id"]: list(r["territory_names"]) for r in old["regions"]
    }
    for terr, (src, dst) in MAP100_CORRECTIONS.items():
        assert terr in membership[src], (terr, src)
        membership[src].remove(terr)
        membership[dst].append(terr)

    for r in old["regions"]:
        rid = r["region_id"]
        old_conf = r["confidence"]
        if rid in MAP100_RF_SPAN:
            name = _name_obj(
                "Regnum Francorum", "printed-unbindable", "medium",
                "printed once (bbox [823,38,124,45]) centred above FOUR "
                "numerals and four fills (regions 6-9); an umbrella label "
                "for the Frankish complex, bindable to no single region",
                spans=list(MAP100_RF_SPAN),
            )
        elif rid in MAP100_BYZ_SPAN:
            name = _name_obj(
                "Byzantine", "printed-unbindable", "low",
                "the printed word sits 2 px from the midpoint of the "
                "numerals of regions 13 and 14 (74.3 px vs 77.6 px); "
                "red-team D5: most likely it labels BOTH regions, matching "
                "the Regnum Francorum precedent -- see unresolved",
                spans=list(MAP100_BYZ_SPAN),
            )
        elif rid in MAP100_NONE_PRINTED:
            name = _name_obj(
                None, "none-printed", "high",
                "no name printed beside this numeral",
            )
        else:
            name = _name_obj(r["name"], "printed", "high", "")
        members = []
        for n in membership[rid]:
            conf = old_conf
            note = ""
            if n in MAP100_CORRECTIONS:
                conf = "high"
                note = CORRECTION_NOTE[n]
            elif n in MAP100_MEMBER_NOTES:
                note = MAP100_MEMBER_NOTES[n]
            members.append(_member(n, conf, "colour-match", note))
        doc["regions"].append(
            {
                "region_id": rid,
                "name": name,
                "kind": r["kind"],
                "layer": "base",
                # red-team: all 24 numerals independently re-detected and
                # re-read; bboxes match to the pixel, values identical
                "bonus": _bonus(r, "high"),
                "association": _association(r, old_conf, r["note"]),
                "colour_hex": r["colour_hex"],
                "members": members,
            }
        )

    for eb in old["extra_bonuses"]:
        if eb["description_verbatim"] == "+1":
            ev = "position of the '+1' glyph over this grey territory in " \
                 "the mini-map (read at 3x zoom)"
        else:
            ev = "italic-serif typography of exactly five main-map labels " \
                 "(INFERRED, not transcribed -- see unresolved)"
        doc["extra_bonuses"].append(
            {
                "description_verbatim": eb["description_verbatim"],
                "value": eb["value"],
                "scope": eb["scope"],
                "members": [
                    {"name": n, "confidence": eb["confidence"], "evidence": ev,
                     "note": ""}
                    for n in eb["territory_names"]
                ],
                "bbox": eb["bbox"],
                "confidence": eb["confidence"],
                "note": eb["note"],
            }
        )

    doc["special_rules"] = [
        {
            # red-team D14: printed on two lines; the line break is part of
            # the verbatim text
            "text_verbatim": "+1 for controlling a grey territory\n"
            "together with an adjacent region",
            "bbox": [687, 321, 286, 37],
            "applies_to": "the nine grey territories (instantiated per "
            "territory in extra_bonuses)",
            "machine_readable": None,
        },
        {
            "text_verbatim": "+5 for controlling all 5 Holy Cities",
            "bbox": [1043, 338, 295, 20],
            "applies_to": "the five italic-serif-labelled territories "
            "(see the matching extra_bonuses set entry)",
            "machine_readable": None,
        },
    ]

    doc["territories_in_no_region"] = [
        {
            "name": n,
            "confidence": "high",
            "evidence": MAP100_ISLAND_SAMPLES.get(
                n, "grey fill on the main map and mini-map"
            ),
        }
        for n in old["territories_in_no_region"]
    ]

    doc["unresolved"] = [
        {
            "what": "the printed label 'Byzantine' may span regions 13 AND "
            "14 rather than naming one of them",
            "options": [
                "spans both 13 and 14 (red-team D5: most likely; matches "
                "the Regnum Francorum precedent and ~900 AD history)",
                "names region 13 only (the '3'; what the pilot's human "
                "calibration read)",
                "names region 14 only (the '4'; the pilot binding -- least "
                "supported: it requires a label position with no precedent "
                "on this map)",
            ],
            "why": "the word's centre is 2 px from the midpoint of the two "
            "numerals (74.3 px vs 77.6 px); no on-map cue binds it to one",
        },
        {
            "what": "the printed label 'Regnum Francorum' spans regions "
            "6-9 rather than naming one region",
            "options": [
                "umbrella label for all four Frankish fills (recorded)",
                "name of a single one of the four regions",
            ],
            "why": "printed once, centred above four numerals (3, 2, 3, 5) "
            "and four distinct fills",
        },
        {
            "what": "Holy Cities membership is inferred from italic-serif "
            "typography, not transcribed",
            "options": [
                "the five italic labels are the five Holy Cities (recorded)",
                "the italic face also marks decoration ('Caspian', 'Oghuz'), "
                "so a typographic rule may over- or under-select",
            ],
            "why": "the printed rule names no cities (red-team D12: "
            "exhaustiveness of 'exactly five' not independently verified)",
        },
        {
            "what": "the grey-territory rule is stored twice: verbatim in "
            "special_rules[0] and as nine per-territory extra_bonuses "
            "instances (same for the Holy-Cities rule and extra_bonuses[9])",
            "options": [
                "consumers must treat extra_bonuses as the per-instance "
                "expansion of the special_rules prose, not additional value",
            ],
            "why": "red-team D11: there is no linkage field; summing both "
            "double-counts",
        },
        {
            "what": "'Map created by Dima' is printed vertically inside "
            "legend_bboxes[0] (x~1330-1345, y~60-340) and is not "
            "transcribed anywhere",
            "options": [],
            "why": "red-team D13: no game impact, but the legend panel is "
            "not fully transcribed",
        },
        {
            "what": "regions 15 (Umayyad) and 22 share the exact fill "
            "colour #8ca05a; colour alone cannot key regions on this map",
            "options": [],
            "why": "the two are distinguished only by position; any "
            "colour-keyed join will conflate them",
        },
        {
            "what": "regions 1 and 4 sit at the map's north-east edge where "
            "the parchment/forest border overlay tints the fills",
            "options": [],
            "why": "membership there is medium confidence, not high",
        },
        {
            "what": "legend numeral bboxes are glyph-tight (connected-"
            "component detection); panel and prose bboxes were estimated "
            "by eye at 3-5x zoom (+/-5 px)",
            "options": [],
            "why": "pilot measurement note, carried over",
        },
        {
            "what": "sample-point caveats: only Baleares, Bari and Crete "
            "sample open sea; Cyprus (pc 0.22) and Billungermark (pc 0.12) "
            "are ambiguous",
            "options": [],
            "why": "replaces the pilot's 'anchors land on open water' list, "
            "which red-team D7 measured to be mostly wrong (all ten "
            "'near-boundary' territories sample at pc 1.00 and agree)",
        },
    ]
    return doc


# --------------------------------------------------------------------------
# map 25 (no pilot legend; map_specific derived from D12's own markup)
# --------------------------------------------------------------------------

def derive_map25_classes(html: str) -> dict[str, list[str]]:
    """BFS the directed adjacency graph into three cyclic classes.

    Bidirectional edge => same class; one-way A->B => class(B) =
    class(A)+1 mod 3.  Deterministic: start at the smallest territory id
    with class 0, visit neighbours in sorted order.
    """
    topo = parse_topology(html, 25)
    names = {t.territory_id: t.name for t in topo.territories}
    directed = {(t.territory_id, a) for t in topo.territories for a in t.adjacencies}
    adj: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for a, b in directed:
        delta = 0 if (b, a) in directed else 1
        adj[a].append((b, delta))
        adj[b].append((a, -delta))
    cls: dict[int, int] = {}
    start = min(names)
    cls[start] = 0
    queue = deque([start])
    while queue:
        u = queue.popleft()
        for v, d in sorted(adj[u]):
            want = (cls[u] + d) % 3
            if v not in cls:
                cls[v] = want
                queue.append(v)
            elif cls[v] != want:
                raise ValueError(
                    f"map 25 directed graph violates the 3-class cyclic "
                    f"model at edge {names[u]!r}->{names[v]!r}"
                )
    # verify every directed constraint, not just the BFS tree
    violations = [
        (a, b)
        for a, b in directed
        if (cls[a] != cls[b] if (b, a) in directed
            else cls[b] != (cls[a] + 1) % 3)
    ]
    if violations:
        raise ValueError(f"map 25 class check failed on {len(violations)} edge(s)")
    return {
        f"class_{k}": sorted(names[t] for t in cls if cls[t] == k)
        for k in range(3)
    }


def build_map25(html: str) -> dict:
    classes = derive_map25_classes(html)
    return {
        "schema_version": 3,
        "map_id": 25,
        "legend_bboxes": [
            {
                "what": "unit-attack legend (framed box: CAVALRY / CANNOT "
                "ATTACK / ARTILLERY / CANNOT ATTACK / INFANTRY / CANNOT "
                "ATTACK / CAVALRY, each unit line flanked by its icon)",
                "bbox": [14, 155, 205, 219],
            },
            {
                "what": "continent-bonus inset (bonus values 2,2,4,3,3,3,3,"
                "2,4) -- NOT yet read into regions",
                "bbox": [15, 390, 270, 190],
            },
        ],
        "regions": [],
        "extra_bonuses": [],
        "special_rules": [
            {
                "text_verbatim": "CAVALRY\nCANNOT ATTACK\nARTILLERY\n"
                "CANNOT ATTACK\nINFANTRY\nCANNOT ATTACK\nCAVALRY",
                "bbox": [14, 155, 205, 219],
                "applies_to": "every territory, via the unit icon painted "
                "on it (the 'Advanced' mechanic)",
                "machine_readable": None,
            }
        ],
        "territories_in_no_region": [],
        "map_specific": {
            "documentation": {
                "unit_classes": "the three cyclic unit classes derived "
                "from D12's directed adjacency markup by BFS "
                "(bidirectional edge => same class; one-way A->B => "
                "class(B) = class(A)+1 mod 3). All 146 directed edges fit "
                "with zero violations (classes of 18/18/19). Recorded as "
                "class_0/1/2, NOT as cavalry/infantry/artillery: binding a "
                "class to a printed unit needs the painted per-territory "
                "icons, and only a partial spot-check of those exists.",
                "unit_attack_rule": "how the classes constrain attacks: a "
                "territory of class k may attack an adjacent territory of "
                "class k (bidirectional edges) or class (k+1) mod 3 "
                "(one-way edges), and cannot attack class (k+2) mod 3. "
                "This is the machine-readable form of the legend's "
                "CANNOT-ATTACK cycle; the special_rules prose stays "
                "authoritative.",
            },
            "unit_classes": classes,
            "unit_attack_rule": {
                "cycle": "class k attacks class k and class (k+1) mod 3; "
                "cannot attack class (k+2) mod 3",
                "derived_from": "data/raw/saved_pages/25.html directed "
                "adjacencies: 55 territories, 146 directed entries "
                "(102 one-way + 22 bidirectional pairs), 0 violations",
            },
        },
        "unresolved": [
            {
                "what": "which unit (cavalry/artillery/infantry) each of "
                "class_0/1/2 is",
                "options": [
                    "read every painted territory icon and bind the "
                    "classes (a partial spot-check of ~40 icons matched "
                    "the derived classes, but no full read exists)",
                ],
                "why": "the directed graph determines the classes only up "
                "to a global rotation of the three unit types",
            },
            {
                "what": "the continent-bonus inset (9 values) has not been "
                "read into regions",
                "options": [],
                "why": "no legend read has been authored for map 25; "
                "merge_legend is out of scope for this map until then",
            },
        ],
    }


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", type=pathlib.Path, required=True,
                    help="directory holding the pilot legend-map{1,7,100}.json")
    args = ap.parse_args(argv)

    converters = {1: convert_map1, 7: convert_map7, 100: convert_map100}
    for map_id, convert in converters.items():
        old = json.loads((args.src / f"legend-map{map_id}.json").read_text())
        doc = convert(old)
        validate_legend_v3(doc)
        out = OUT_ROOT / str(map_id) / f"legend-map{map_id}.json"
        out.write_text(json.dumps(doc, indent=1))
        n_members = sum(len(r["members"]) for r in doc["regions"])
        print(f"map {map_id}: {len(doc['regions'])} regions, "
              f"{n_members} memberships -> {out}")

    html = (REPO_ROOT / "data" / "raw" / "saved_pages" / "25.html").read_text()
    doc25 = build_map25(html)
    validate_legend_v3(doc25)
    out25 = OUT_ROOT / "25" / "legend-map25.json"
    out25.write_text(json.dumps(doc25, indent=1))
    sizes = {k: len(v) for k, v in doc25["map_specific"]["unit_classes"].items()}
    print(f"map 25: unit classes {sizes} -> {out25}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
