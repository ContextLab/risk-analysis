"""Legend/region schema v3: validation and loading.

Why v3 exists (issue #4): a 3-map pilot read bonus legends into a draft
schema, and an adversarial review then found four membership errors on map
100 whose territory totals reconciled identically before and after -- the
aggregate count check cannot detect a swap.  v3 closes three gaps before
the schema fans out to 75 more maps:

1. ``confidence`` was one scalar answering three questions.  v3 splits it:
   ``bonus.confidence`` (is the VALUE right), ``association.confidence``
   (is this region correctly tied to its legend entry), and per-member
   ``confidence`` (is THIS territory in THIS region).
2. ``name: null`` conflated four states.  v3 names are objects
   ``{text, provenance, spans_region_ids, confidence, note}`` with
   provenance one of ``printed`` (transcribed verbatim),
   ``printed-unbindable`` (printed but not attributable to a single
   region -- ``spans_region_ids`` lists the candidate regions),
   ``inferred`` (never printed; reader-derived, auditable), and
   ``none-printed`` (nothing printed; text must be null).
3. Members were bare strings.  v3 members are objects carrying their own
   evidence and the colour-cluster cross-check result.  A bare-string
   member is REJECTED, never silently upgraded.

``layer`` distinguishes ``base`` colour regions from ``overlay`` bonus
groups (map 7's six city bonuses overlay its six colour regions: 44
memberships across 35 territories, which without a layer marker looks
like data corruption).  A territory may legally be in one base region and
one or more overlay regions; two BASE regions claiming the same territory
is an error.

``colour_hex`` is descriptive only and must never be used as a join key:
measured on map 100, within-region colour variation reaches 42 RGB while
between-region separation is 0-17 (two regions share the exact fill).

``map_specific`` is the load-bearing escape hatch for rules no general
schema anticipates (map 25's rock-paper-scissors unit-attack cycle).  It
is free-form per map but every key must be documented in prose in the
same file (the ``documentation`` sub-dict), it is NEVER required, and no
code may assume any particular key exists.  ``special_rules[].
machine_readable`` is likewise optional; the verbatim prose stays
authoritative and a structured interpretation never replaces it.

Validation collects every structural problem and raises one loud
ValueError; it never repairs anything.
"""
from __future__ import annotations

import json
import pathlib
import re

from riskdyn.workbench.build import AUTHORED_ROOT
from riskdyn.workbench.score import normalize_name

CONFIDENCE_LEVELS = {"high", "medium", "low"}
NAME_PROVENANCES = {"printed", "printed-unbindable", "inferred", "none-printed"}
REGION_KINDS = {"colour-region", "bonus-group", "other"}
LAYERS = {"base", "overlay"}
ASSOCIATION_METHODS = {"by-colour", "by-name", "by-list", "by-legend-position"}
MEMBER_EVIDENCE = {"colour-match", "legend-list", "label-position", "adjacency"}

_HEX_RE = re.compile(r"^#[0-9a-f]{6}$")


def legend_path(map_id: int, authored_root: pathlib.Path | None = None) -> pathlib.Path:
    return (authored_root or AUTHORED_ROOT) / str(map_id) / f"legend-map{map_id}.json"


def _is_bbox(value) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 4
        and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)
    )


def _check_confidence(value, where: str, problems: list[str]) -> None:
    if value not in CONFIDENCE_LEVELS:
        problems.append(
            f"{where}: confidence must be one of {sorted(CONFIDENCE_LEVELS)}, "
            f"got {value!r}"
        )


def _check_name(name, rid, region_ids: set, problems: list[str]) -> None:
    where = f"region {rid} name"
    if not isinstance(name, dict):
        problems.append(
            f"{where}: must be an object {{text, provenance, spans_region_ids, "
            f"confidence, note}} in schema v3, got {name!r} (a bare string or "
            "null name is the retired v2 form; no silent upgrade)"
        )
        return
    text = name.get("text")
    provenance = name.get("provenance")
    spans = name.get("spans_region_ids")
    if provenance not in NAME_PROVENANCES:
        problems.append(
            f"{where}: provenance must be one of {sorted(NAME_PROVENANCES)}, "
            f"got {provenance!r}"
        )
        return
    if provenance == "none-printed":
        if text is not None:
            problems.append(
                f"{where}: provenance 'none-printed' requires text null, "
                f"got {text!r}"
            )
    else:
        if not (isinstance(text, str) and text.strip()):
            problems.append(
                f"{where}: provenance {provenance!r} requires non-empty text, "
                f"got {text!r}"
            )
    if provenance == "printed-unbindable":
        if not (isinstance(spans, list) and spans and
                all(isinstance(s, int) for s in spans)):
            problems.append(
                f"{where}: provenance 'printed-unbindable' requires "
                "spans_region_ids to list the candidate region ids the "
                f"printed label may cover, got {spans!r}"
            )
        else:
            if rid not in spans:
                problems.append(
                    f"{where}: spans_region_ids {spans} must include this "
                    f"region's own id {rid}"
                )
            unknown = [s for s in spans if s not in region_ids]
            if unknown:
                problems.append(
                    f"{where}: spans_region_ids references unknown region "
                    f"id(s) {unknown}"
                )
    elif spans not in (None, []):
        problems.append(
            f"{where}: spans_region_ids is only meaningful for provenance "
            f"'printed-unbindable', got {spans!r} with provenance "
            f"{provenance!r}"
        )
    _check_confidence(name.get("confidence"), where, problems)


def _check_member(member, rid, problems: list[str]) -> None:
    where = f"region {rid}"
    if isinstance(member, str):
        problems.append(
            f"{where} member {member!r} is a bare string; schema v3 members "
            "are objects {name, confidence, evidence, colour_cluster, "
            "agrees_with_cluster, note} -- no silent upgrade from the v2 "
            "territory_names form"
        )
        return
    if not isinstance(member, dict):
        problems.append(f"{where} member {member!r} is not an object")
        return
    mname = member.get("name")
    if not (isinstance(mname, str) and mname.strip()):
        problems.append(f"{where} member has no usable name: {member!r}")
    _check_confidence(member.get("confidence"), f"{where} member {mname!r}", problems)
    if member.get("evidence") not in MEMBER_EVIDENCE:
        problems.append(
            f"{where} member {mname!r}: evidence must be one of "
            f"{sorted(MEMBER_EVIDENCE)}, got {member.get('evidence')!r}"
        )
    cc = member.get("colour_cluster")
    if cc is not None and not isinstance(cc, int):
        problems.append(
            f"{where} member {mname!r}: colour_cluster must be an int or "
            f"null, got {cc!r}"
        )
    awc = member.get("agrees_with_cluster")
    if awc is not None and not isinstance(awc, bool):
        problems.append(
            f"{where} member {mname!r}: agrees_with_cluster must be true/"
            f"false/null, got {awc!r}"
        )


def validate_legend_v3(doc: dict) -> None:
    """All structural problems at once, as one loud ValueError."""
    problems: list[str] = []
    if doc.get("schema_version") != 3:
        raise ValueError(
            f"legend document has schema_version "
            f"{doc.get('schema_version')!r}; this validator accepts only "
            "schema_version 3 (there is no silent upgrade path -- migrate "
            "the file explicitly)"
        )
    if not isinstance(doc.get("map_id"), int):
        problems.append(f"map_id must be an int, got {doc.get('map_id')!r}")

    for i, lb in enumerate(doc.get("legend_bboxes", [])):
        if not (isinstance(lb, dict) and isinstance(lb.get("what"), str)
                and _is_bbox(lb.get("bbox"))):
            problems.append(f"legend_bboxes[{i}] must be {{what, bbox[4]}}: {lb!r}")

    regions = doc.get("regions")
    if not isinstance(regions, list):
        problems.append("'regions' must be a list")
        regions = []
    region_ids = {r.get("region_id") for r in regions if isinstance(r, dict)}
    seen_rids: set = set()
    # normalized member name -> region id, per layer (a territory may sit in
    # one base region AND one or more overlay regions; two regions of the
    # SAME layer claiming it is the data-corruption case `layer` exists to
    # rule out)
    seen_members: dict[str, dict[str, int]] = {"base": {}, "overlay": {}}
    for r in regions:
        if not isinstance(r, dict):
            problems.append(f"region entry is not an object: {r!r}")
            continue
        rid = r.get("region_id")
        if not isinstance(rid, int):
            problems.append(f"region has non-int region_id {rid!r}")
        if rid in seen_rids:
            problems.append(f"duplicate region_id {rid}")
        seen_rids.add(rid)
        _check_name(r.get("name"), rid, region_ids, problems)
        if r.get("kind") not in REGION_KINDS:
            problems.append(
                f"region {rid}: kind must be one of {sorted(REGION_KINDS)}, "
                f"got {r.get('kind')!r}"
            )
        layer = r.get("layer")
        if layer not in LAYERS:
            problems.append(
                f"region {rid}: layer must be one of {sorted(LAYERS)}, "
                f"got {layer!r}"
            )
        bonus = r.get("bonus")
        if not isinstance(bonus, dict):
            problems.append(
                f"region {rid}: bonus must be an object {{value, "
                f"text_verbatim, bbox, confidence}}, got {bonus!r}"
            )
        else:
            v = bonus.get("value")
            if v is not None and not isinstance(v, int):
                problems.append(f"region {rid}: bonus.value must be int or null, got {v!r}")
            if bonus.get("bbox") is not None and not _is_bbox(bonus.get("bbox")):
                problems.append(f"region {rid}: bonus.bbox must be [x,y,w,h] or null")
            _check_confidence(bonus.get("confidence"), f"region {rid} bonus", problems)
        assoc = r.get("association")
        if not isinstance(assoc, dict):
            problems.append(
                f"region {rid}: association must be an object {{method, "
                f"confidence, note}}, got {assoc!r}"
            )
        else:
            if assoc.get("method") not in ASSOCIATION_METHODS:
                problems.append(
                    f"region {rid}: association.method must be one of "
                    f"{sorted(ASSOCIATION_METHODS)}, got {assoc.get('method')!r}"
                )
            _check_confidence(assoc.get("confidence"), f"region {rid} association", problems)
        hexv = r.get("colour_hex")
        if hexv is not None and not (isinstance(hexv, str) and _HEX_RE.match(hexv)):
            problems.append(
                f"region {rid}: colour_hex must be '#rrggbb' or null, got "
                f"{hexv!r} (descriptive only; never a join key)"
            )
        members = r.get("members")
        if not isinstance(members, list):
            problems.append(f"region {rid}: 'members' must be a list, got {members!r}")
            members = []
        local: set[str] = set()
        for m in members:
            _check_member(m, rid, problems)
            if isinstance(m, dict) and isinstance(m.get("name"), str):
                norm = normalize_name(m["name"])
                if norm in local:
                    problems.append(
                        f"region {rid} lists member {m['name']!r} more than once"
                    )
                local.add(norm)
                if layer in seen_members:
                    other = seen_members[layer].get(norm)
                    if other is not None and other != rid:
                        problems.append(
                            f"territory {m['name']!r} is claimed by two "
                            f"{layer}-layer regions ({other} and {rid}); a "
                            "territory may sit in at most one region per layer"
                        )
                    seen_members[layer][norm] = rid

    for i, eb in enumerate(doc.get("extra_bonuses", [])):
        where = f"extra_bonuses[{i}]"
        if not isinstance(eb, dict):
            problems.append(f"{where} is not an object: {eb!r}")
            continue
        if not (isinstance(eb.get("description_verbatim"), str)
                and eb["description_verbatim"].strip()):
            problems.append(f"{where}: description_verbatim must be non-empty text")
        if not isinstance(eb.get("value"), (int, float)) or isinstance(eb.get("value"), bool):
            problems.append(f"{where}: value must be a number, got {eb.get('value')!r}")
        _check_confidence(eb.get("confidence"), where, problems)
        for m in eb.get("members", []):
            if isinstance(m, str):
                problems.append(
                    f"{where} member {m!r} is a bare string; v3 members are "
                    "objects {name, confidence, evidence, note}"
                )
            elif isinstance(m, dict):
                if not (isinstance(m.get("name"), str) and m["name"].strip()):
                    problems.append(f"{where} member has no usable name: {m!r}")
                _check_confidence(m.get("confidence"), f"{where} member", problems)
                if not (isinstance(m.get("evidence"), str) and m["evidence"].strip()):
                    problems.append(
                        f"{where} member {m.get('name')!r}: evidence must be "
                        "non-empty text"
                    )
            else:
                problems.append(f"{where} member is not an object: {m!r}")

    for i, sr in enumerate(doc.get("special_rules", [])):
        where = f"special_rules[{i}]"
        if not isinstance(sr, dict):
            problems.append(f"{where} is not an object: {sr!r}")
            continue
        if not (isinstance(sr.get("text_verbatim"), str) and sr["text_verbatim"].strip()):
            problems.append(f"{where}: text_verbatim must be non-empty text")
        # machine_readable is OPTIONAL and null is always legal: the verbatim
        # prose is authoritative and a structured interpretation never
        # replaces it.

    for i, t in enumerate(doc.get("territories_in_no_region", [])):
        where = f"territories_in_no_region[{i}]"
        if isinstance(t, str):
            problems.append(
                f"{where} {t!r} is a bare string; v3 entries are objects "
                "{name, confidence, evidence}"
            )
        elif isinstance(t, dict):
            if not (isinstance(t.get("name"), str) and t["name"].strip()):
                problems.append(f"{where}: name must be non-empty text")
            _check_confidence(t.get("confidence"), where, problems)
            if not (isinstance(t.get("evidence"), str) and t["evidence"].strip()):
                problems.append(f"{where}: evidence must be non-empty text")
        else:
            problems.append(f"{where} is not an object: {t!r}")

    # map_specific is NEVER required: absent and {} are equally legal, and
    # no code may assume any particular key exists inside it.
    ms = doc.get("map_specific", {})
    if not isinstance(ms, dict):
        problems.append(f"'map_specific' must be an object, got {ms!r}")
    else:
        keys = set(ms) - {"documentation"}
        docs = ms.get("documentation")
        if keys:
            if not isinstance(docs, dict):
                problems.append(
                    "map_specific carries keys "
                    f"{sorted(keys)} but no 'documentation' object; every "
                    "map_specific key must be documented in prose in the "
                    "same file"
                )
            else:
                for k in sorted(keys):
                    if not (isinstance(docs.get(k), str) and docs[k].strip()):
                        problems.append(
                            f"map_specific key {k!r} has no prose entry in "
                            "map_specific.documentation"
                        )
                for k in sorted(set(docs) - keys):
                    problems.append(
                        f"map_specific.documentation documents {k!r} which "
                        "does not exist in map_specific"
                    )

    for i, u in enumerate(doc.get("unresolved", [])):
        where = f"unresolved[{i}]"
        if not isinstance(u, dict):
            problems.append(f"{where} is not an object: {u!r}")
            continue
        if not (isinstance(u.get("what"), str) and u["what"].strip()):
            problems.append(f"{where}: 'what' must be non-empty text")
        if not isinstance(u.get("options"), list):
            problems.append(f"{where}: 'options' must be a list (may be empty)")
        if not isinstance(u.get("why"), str):
            problems.append(f"{where}: 'why' must be a string (may be empty)")

    if problems:
        raise ValueError(
            f"legend for map {doc.get('map_id')} is invalid "
            f"({len(problems)} problem(s)):\n  " + "\n  ".join(problems)
        )


def load_legend_v3(path: pathlib.Path, map_id: int) -> dict:
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} not found: no v3 legend has been authored for map "
            f"{map_id}, so this map is out of scope for merge_legend until "
            "one exists (the colour-sample proposals in region_sample.json "
            "cannot be related to legend region ids on their own)"
        )
    doc = json.loads(path.read_text())
    validate_legend_v3(doc)
    if doc["map_id"] != map_id:
        raise ValueError(f"{path} declares map_id {doc['map_id']}, expected {map_id}")
    return doc
