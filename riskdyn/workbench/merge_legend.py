"""Merge a v3 legend read into annotations.json, gated by a colour cross-check.

    ./.venv/bin/python -m riskdyn.workbench.merge_legend <map_id> [--force]

Reads ``data/authored/maps/<id>/legend-map<id>.json`` (schema v3, see
``riskdyn.workbench.legend_schema``) plus the colour-sample report
``data/processed/maps/<id>/region_sample.json``, and writes ``regions``
and per-territory ``region_ids`` into
``data/authored/maps/<id>/annotations.json``.

The gate
    Why it exists: on map 100 an adversarial review found four membership
    errors that left every aggregate territory count IDENTICAL -- a swap
    between regions is invisible to count reconciliation.  The only check
    that caught them was per-territory comparison against independently
    derived colour clusters, so that comparison is a hard gate here.

    ``agrees_with_cluster`` is computed by this tool for every member,
    NEVER trusted from the input file.  For a base-layer member the rule
    is: the member agrees when its colour cluster's majority base
    assignment equals its legend region.  Majority is the plurality of
    the cluster's members' base assignments (a territory in no region
    counts as the value "none").  Two deliberate refinements:

    * A tied plurality means the colour evidence corroborates neither
      side.  The tool still needs a deterministic verdict, so ties
      resolve to "no region" first (the conservative reading: colour
      cannot even establish region membership) and then to the smallest
      region id; every member flagged through a tie carries
      ``ambiguous_majority: true`` in the conflict record so a human sees
      the tie rather than a clean majority.
    * A member alone in a singleton cluster is a DISAGREEMENT whenever
      its region has other members: the colour method separated this
      territory from every claimed region-mate, which is exactly the
      per-territory signal the aggregate checks cannot see.  (A region
      whose only member is its own singleton cluster agrees -- both
      methods isolate it.)

    Overlay-layer members (bonus groups drawn over base colour regions)
    get ``agrees_with_cluster: null``: their artwork colour IS the base
    region's colour, so the cluster check has no bearing on overlay
    membership and a boolean would be fabricated evidence.

    If any member disagrees, the merge REFUSES and writes the
    disagreements to ``data/processed/maps/<id>/region_conflicts.json``.
    ``--force`` proceeds but marks every conflicting member
    ``confidence: "low"`` and records each conflict in the legend's
    ``unresolved`` list.  Refusing is the correct outcome for
    unadjudicated disagreements -- do not resolve them here.

Human sign-off is inviolable
    Colour-sample regions written by ``riskdyn.workbench.regions
    --write`` are proposals and a legend merge may overwrite them, but a
    member whose confidence came from a human is NEVER overwritten, with
    or without ``--force``.  A member counts as human-signed when the
    annotations' ``verification.regions_confirmed`` block carries an
    explicit human sign-off (agents never count), or when the territory's
    own ``note``/``evidence`` text records one (matches
    ``HUMAN_SIGNOFF_RE``).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from collections import Counter, defaultdict

from riskdyn.segment import catalog as cat
from riskdyn.workbench.build import AUTHORED_ROOT, PROCESSED_ROOT
from riskdyn.workbench.graph_build import (
    _human_signoff,
    _summary_or_fallback,
    load_annotations_v2,
    validate_annotations,
)
from riskdyn.workbench.legend_schema import legend_path, load_legend_v3
from riskdyn.workbench.score import normalize_name

HUMAN_SIGNOFF_RE = re.compile(
    r"human[\s-]*(verified|confirmed|sign[\s-]*off)", re.IGNORECASE
)

_CONF_ORDER = {"low": 0, "medium": 1, "high": 2}


class MergeRefused(RuntimeError):
    """The merge did not happen; the message says exactly why."""


def _member_is_human_protected(territory: dict, verification: dict) -> bool:
    """True when this territory's region membership carries human sign-off.

    Two sources count: an explicit human sign-off in the annotations'
    ``verification.regions_confirmed`` block (which covers every member),
    or human sign-off recorded in the territory's own ``note`` or
    ``evidence`` text.  Agent attributions never count (see
    ``graph_build._human_signoff``).
    """
    if _human_signoff((verification or {}).get("regions_confirmed")):
        return True
    for key in ("note", "evidence"):
        value = territory.get(key)
        if isinstance(value, str) and HUMAN_SIGNOFF_RE.search(value):
            return True
    return False


# --------------------------------------------------------------------------
# name resolution
# --------------------------------------------------------------------------

def _resolve_names(legend: dict, territories: list[dict]) -> dict[str, int]:
    """normalized member name -> territory_id; loud on any mismatch.

    Every legend member and no-region entry must match exactly one
    annotations territory, and every annotations territory must be
    accounted for by exactly one base-layer region or the no-region list.
    """
    problems: list[str] = []
    norm_to_tid = {normalize_name(t["name"]): t["territory_id"] for t in territories}
    resolved: dict[str, int] = {}
    base_owner: dict[str, str] = {}  # norm -> where it was claimed

    def claim(name: str, where: str, is_base: bool) -> None:
        norm = normalize_name(name)
        tid = norm_to_tid.get(norm)
        if tid is None:
            problems.append(
                f"{where} names territory {name!r} which matches no "
                "annotations territory (normalized comparison)"
            )
            return
        resolved[norm] = tid
        if is_base:
            if norm in base_owner:
                problems.append(
                    f"territory {name!r} is claimed by both "
                    f"{base_owner[norm]} and {where}"
                )
            base_owner[norm] = where

    for r in legend["regions"]:
        is_base = r["layer"] == "base"
        for m in r["members"]:
            claim(m["name"], f"region {r['region_id']} ({r['layer']})", is_base)
    for t in legend.get("territories_in_no_region", []):
        claim(t["name"], "territories_in_no_region", True)

    unaccounted = sorted(
        norm for norm in norm_to_tid if norm not in base_owner
    )
    if unaccounted:
        problems.append(
            f"{len(unaccounted)} annotations territor(ies) appear in no "
            "base-layer region and not in territories_in_no_region: "
            + ", ".join(unaccounted)
        )
    if problems:
        raise MergeRefused(
            f"legend and annotations for map {legend['map_id']} cannot be "
            f"reconciled ({len(problems)} problem(s)):\n  "
            + "\n  ".join(problems)
        )
    return resolved


# --------------------------------------------------------------------------
# the colour cross-check
# --------------------------------------------------------------------------

def cross_check(
    legend: dict, sample: dict, norm_to_tid: dict[str, int]
) -> tuple[dict[int, dict], list[dict]]:
    """Compute agrees_with_cluster for every member; return (per-member
    results keyed by territory_id, conflict records).

    Results carry {cluster_id, agrees (bool|None), majority_region,
    ambiguous_majority}; conflicts are the base members whose agrees is
    False.
    """
    tid_cluster = {t["territory_id"]: t["cluster_id"] for t in sample["territories"]}
    tid_info = {t["territory_id"]: t for t in sample["territories"]}

    base_region: dict[int, int | None] = {}
    region_member_count: Counter = Counter()
    for r in legend["regions"]:
        if r["layer"] != "base":
            continue
        for m in r["members"]:
            tid = norm_to_tid[normalize_name(m["name"])]
            base_region[tid] = r["region_id"]
            region_member_count[r["region_id"]] += 1
    for t in legend.get("territories_in_no_region", []):
        base_region[norm_to_tid[normalize_name(t["name"])]] = None

    missing = sorted(t for t in base_region if t not in tid_cluster)
    if missing:
        raise MergeRefused(
            "region_sample.json does not cover territory id(s) "
            f"{missing}; re-run riskdyn.workbench.regions for this map"
        )

    cluster_members: dict[int, list[int]] = defaultdict(list)
    for tid, cid in tid_cluster.items():
        cluster_members[cid].append(tid)

    def majority(cid: int) -> tuple[int | None, bool]:
        """(majority base assignment, was_tied).  Ties resolve to no-region
        first (colour cannot even establish membership), then smallest
        region id -- deterministic, and flagged as ambiguous."""
        counts = Counter(base_region[t] for t in cluster_members[cid])
        top = max(counts.values())
        tied = sorted(
            (v for v, n in counts.items() if n == top),
            key=lambda v: (v is not None, v if v is not None else 0),
        )
        return tied[0], len(tied) > 1

    results: dict[int, dict] = {}
    conflicts: list[dict] = []
    names_by_tid = {
        norm_to_tid[normalize_name(m["name"])]: m["name"]
        for r in legend["regions"]
        for m in r["members"]
    }
    for r in legend["regions"]:
        for m in r["members"]:
            tid = norm_to_tid[normalize_name(m["name"])]
            cid = tid_cluster[tid]
            if r["layer"] != "base":
                # overlay colours ARE the base colours; a boolean here
                # would be fabricated evidence
                results.setdefault(
                    tid, {"cluster_id": cid, "agrees": None,
                          "majority_region": None, "ambiguous_majority": False},
                )
                continue
            maj, ambiguous = majority(cid)
            if len(cluster_members[cid]) == 1:
                # colour isolates this territory from every claimed
                # region-mate; that IS a per-territory disagreement unless
                # the region has no other members
                agrees = region_member_count[r["region_id"]] == 1
                reason = (
                    "colour cluster is a singleton: the colour method "
                    "separates this territory from every other member of "
                    f"its claimed region {r['region_id']}"
                )
            else:
                agrees = maj == r["region_id"]
                reason = (
                    f"legend region {r['region_id']} but colour cluster "
                    f"{cid}'s majority base assignment is "
                    f"{'no region' if maj is None else f'region {maj}'}"
                    + (" (tied plurality)" if ambiguous else "")
                )
            results[tid] = {
                "cluster_id": cid,
                "agrees": agrees,
                "majority_region": maj,
                "ambiguous_majority": ambiguous,
            }
            if not agrees:
                info = tid_info[tid]
                conflicts.append(
                    {
                        "territory_id": tid,
                        "name": names_by_tid[tid],
                        "legend_region_id": r["region_id"],
                        "cluster_id": cid,
                        "cluster_majority_region_id": maj,
                        "ambiguous_majority": ambiguous,
                        "cluster_size": len(cluster_members[cid]),
                        "patch_consistency": info.get("patch_consistency"),
                        "sample_reliable": info.get("reliable"),
                        "reason": reason,
                    }
                )
    conflicts.sort(key=lambda c: (c["legend_region_id"], c["name"]))
    return results, conflicts


# --------------------------------------------------------------------------
# assembling the annotations update
# --------------------------------------------------------------------------

def _flat_region(r: dict) -> dict:
    """Legend v3 region -> flat schema-v2 annotations region.

    Keeps the keys graph_build's bonuses assembly reads (bonus,
    bonus_text_verbatim, bonus_bbox, association, confidence, source,
    note) while carrying the v3 provenance split alongside.
    """
    name = r["name"]
    return {
        "region_id": r["region_id"],
        "name": name.get("text"),
        "name_provenance": name.get("provenance"),
        "name_spans_region_ids": name.get("spans_region_ids"),
        "kind": r["kind"],
        "layer": r["layer"],
        "bonus": r["bonus"].get("value"),
        "bonus_text_verbatim": r["bonus"].get("text_verbatim"),
        "bonus_bbox": r["bonus"].get("bbox"),
        "association": r["association"].get("method"),
        "association_confidence": r["association"].get("confidence"),
        "colour_hex": r.get("colour_hex"),
        "source": "legend-read",
        "confidence": r["bonus"].get("confidence"),
        "note": (r["association"].get("note") or name.get("note") or ""),
    }


def merge_legend_map(
    map_id: int,
    force: bool = False,
    authored_root: pathlib.Path | None = None,
    out_root: pathlib.Path | None = None,
) -> dict:
    """Merge the v3 legend for one map into its annotations.

    Returns a summary dict.  Raises MergeRefused (leaving annotations and
    the legend file untouched) when the cross-check finds conflicts and
    ``force`` is not given, when a human-signed member would change, or
    when legend and annotations cannot be reconciled.
    """
    authored = authored_root or AUTHORED_ROOT
    out_dir = (out_root or PROCESSED_ROOT) / str(map_id)
    leg_path = legend_path(map_id, authored)
    ann_path = authored / str(map_id) / "annotations.json"
    sample_path = out_dir / "region_sample.json"

    legend = load_legend_v3(leg_path, map_id)
    if not legend["regions"]:
        raise MergeRefused(
            f"{leg_path} defines no regions; there is nothing to merge and "
            "the colour clusters in region_sample.json cannot be related "
            "to legend region ids on their own -- this map is out of scope "
            "for merge_legend until its legend regions are authored"
        )
    ann = load_annotations_v2(ann_path, map_id)
    if not sample_path.is_file():
        raise MergeRefused(
            f"{sample_path} not found: the cross-check gate needs the "
            "colour-sample report; run riskdyn.workbench.regions "
            f"{map_id} first"
        )
    sample = json.loads(sample_path.read_text())

    norm_to_tid = _resolve_names(legend, ann["territories"])
    results, conflicts = cross_check(legend, sample, norm_to_tid)

    # -- write the computed cross-check into the legend members (never
    #    trusted from input, always overwritten with what THIS run measured)
    conflict_tids = {c["territory_id"] for c in conflicts}
    for r in legend["regions"]:
        for m in r["members"]:
            tid = norm_to_tid[normalize_name(m["name"])]
            m["colour_cluster"] = results[tid]["cluster_id"]
            m["agrees_with_cluster"] = (
                results[tid]["agrees"] if r["layer"] == "base" else None
            )

    out_dir.mkdir(parents=True, exist_ok=True)
    conflicts_doc = {
        "map_id": map_id,
        "n_conflicts": len(conflicts),
        "n_members_checked": sum(
            1 for r in legend["regions"] if r["layer"] == "base"
            for _ in r["members"]
        ),
        "rule": (
            "a base-layer member agrees when its colour cluster's majority "
            "base assignment (plurality; ties resolve to no-region first, "
            "then smallest region id, and are flagged ambiguous) equals "
            "its legend region; a member alone in a singleton cluster "
            "disagrees whenever its region has other members"
        ),
        "conflicts": conflicts,
        "note": (
            "unadjudicated disagreements between the legend read and the "
            "independent colour clustering; refusing to merge is correct "
            "until a human adjudicates them"
        ),
    }
    (out_dir / "region_conflicts.json").write_text(json.dumps(conflicts_doc, indent=1))

    if conflicts and not force:
        raise MergeRefused(
            f"map {map_id}: {len(conflicts)} of "
            f"{conflicts_doc['n_members_checked']} legend members disagree "
            "with the independent colour clusters; the merge refuses and "
            f"wrote the disagreements to {out_dir / 'region_conflicts.json'}. "
            "Adjudicate them (fix the legend or the samples), or re-run "
            "with --force to proceed with every conflicting member marked "
            "confidence low and recorded in the legend's unresolved list."
        )

    # -- force: demote conflicting members and record each conflict
    if conflicts and force:
        for r in legend["regions"]:
            for m in r["members"]:
                if norm_to_tid[normalize_name(m["name"])] in conflict_tids:
                    m["confidence"] = "low"
        existing_whats = {u.get("what") for u in legend.get("unresolved", [])}
        for c in conflicts:
            what = (
                f"colour cross-check conflict: {c['name']} "
                f"(legend region {c['legend_region_id']})"
            )
            if what in existing_whats:
                continue
            legend.setdefault("unresolved", []).append(
                {
                    "what": what,
                    "options": [
                        f"legend region {c['legend_region_id']} is right",
                        "the colour cluster assignment is right",
                    ],
                    "why": c["reason"] + "; merged with --force, member "
                    "confidence demoted to low",
                }
            )

    # -- new per-territory region_ids from the legend (base + overlay)
    new_region_ids: dict[int, list[int]] = {t["territory_id"]: [] for t in ann["territories"]}
    member_conf: dict[int, str] = {}
    for r in legend["regions"]:
        for m in r["members"]:
            tid = norm_to_tid[normalize_name(m["name"])]
            new_region_ids[tid].append(r["region_id"])
            conf = m["confidence"]
            if tid not in member_conf or _CONF_ORDER[conf] < _CONF_ORDER[member_conf[tid]]:
                member_conf[tid] = conf
    for tid in new_region_ids:
        new_region_ids[tid] = sorted(new_region_ids[tid])

    # -- the human guard: NEVER overwrite a human-signed member, with or
    #    without --force
    verification = ann.get("verification", {})
    protected_changes = [
        t["name"]
        for t in ann["territories"]
        if sorted(t.get("region_ids") or []) != new_region_ids[t["territory_id"]]
        and _member_is_human_protected(t, verification)
    ]
    if protected_changes:
        raise MergeRefused(
            f"map {map_id}: the merge would change region membership for "
            f"{len(protected_changes)} territor(ies) whose membership "
            "carries HUMAN sign-off: "
            + ", ".join(sorted(protected_changes))
            + ". A legend merge never overwrites a human-confirmed member "
            "(--force does not override this). Remove or update the human "
            "sign-off explicitly if the artwork really disagrees."
        )

    for t in ann["territories"]:
        t["region_ids"] = new_region_ids[t["territory_id"]]
        t["region_source"] = "legend-read"
        if t["territory_id"] in member_conf:
            t["region_confidence"] = member_conf[t["territory_id"]]
        else:
            t.pop("region_confidence", None)
    ann["regions"] = [_flat_region(r) for r in legend["regions"]]

    summary = _summary_or_fallback(map_id)
    validate_annotations(ann, summary.width, summary.height)

    ann_path.write_text(json.dumps(ann, indent=1))
    leg_path.write_text(json.dumps(legend, indent=1))
    return {
        "map_id": map_id,
        "n_regions": len(legend["regions"]),
        "n_memberships": sum(len(r["members"]) for r in legend["regions"]),
        "n_conflicts": len(conflicts),
        "forced": bool(conflicts and force),
        "conflicts_path": str(out_dir / "region_conflicts.json"),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="merge a schema-v3 legend read into annotations.json, "
        "gated by the per-territory colour cross-check"
    )
    ap.add_argument("map_id", type=int)
    ap.add_argument(
        "--force",
        action="store_true",
        help="proceed despite cross-check conflicts; conflicting members "
        "are marked confidence low and recorded in the legend's unresolved "
        "list (human-signed members are still never overwritten)",
    )
    args = ap.parse_args(argv)
    try:
        summary = merge_legend_map(args.map_id, force=args.force)
    except (MergeRefused, FileNotFoundError, ValueError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    print(
        f"map {summary['map_id']}: merged {summary['n_regions']} regions, "
        f"{summary['n_memberships']} memberships, "
        f"{summary['n_conflicts']} conflict(s)"
        + (" (FORCED: conflicting members demoted to low confidence)"
           if summary["forced"] else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
