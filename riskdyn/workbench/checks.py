"""The (a)-(d) acceptance criteria, honestly implemented.

Each check returns ``{"status": "pass" | "fail" | "unverified", ...}``.
``unverified`` is a first-class outcome: a criterion whose evidence the
data cannot support is reported as such, never silently implied to pass.

- (a) no overlapping outlines: measured on the polygons as written.
- (b) all playable territories present: count AND name bijection.  The
  count alone is necessary but NOT sufficient (dropping a real territory
  while admitting a mini-map tile still yields N), so pass requires the
  name evidence; count-only maps are ``unverified``.
- (c) no non-playable regions: semantic.  The automatable part (every
  emitted territory traceable to a ground-truth seed claim) is checked;
  the rest requires a human looking at the overlay, so without a human
  sign-off the criterion is ``unverified`` even when the sub-checks pass.
- (d) bonuses accurate: NO automated ground truth exists.  The check
  validates internal consistency and reports transcription confidence and
  provenance; only an explicit human verification flips it to pass.
"""
from __future__ import annotations

from typing import Any

from riskdyn.workbench.bonuses import validate_bonuses
from riskdyn.workbench.overlap import Ring, measure_overlap

OVERLAP_TOLERANCE_PX2 = 1e-6


def check_a_no_overlap(rings_by_id: dict[int, tuple[Ring, ...]]) -> dict[str, Any]:
    overlap = measure_overlap(rings_by_id)
    return {
        "criterion": "a: territory outlines do not overlap",
        "status": "pass" if overlap["total_px2"] <= OVERLAP_TOLERANCE_PX2 else "fail",
        "overlap_px2": overlap["total_px2"],
        "n_overlapping_pairs": overlap["n_pairs"],
        "worst_pairs": overlap["pairs"][:10],
        "tolerance_px2": OVERLAP_TOLERANCE_PX2,
        "method": "shapely pairwise intersection over the polygons as written",
    }


def check_b_all_present(
    territories: list[dict],
    expected_count: int,
    ground_truth_names: list[str] | None,
) -> dict[str, Any]:
    names = [t.get("name") for t in territories]
    found = len(territories)
    result: dict[str, Any] = {
        "criterion": "b: all playable territories present",
        "expected_count": expected_count,
        "found_count": found,
        "count_matches": found == expected_count,
        "note": (
            "count equality is necessary but NOT sufficient (a dropped real "
            "territory plus an admitted decoration still matches); the name "
            "bijection below is the actual evidence"
        ),
    }
    if ground_truth_names is None:
        result["status"] = "fail" if found != expected_count else "unverified"
        result["name_evidence"] = (
            "no ground-truth name list for this map; count-only evidence "
            "cannot prove presence"
        )
        return result
    gt = sorted(ground_truth_names)
    missing = sorted(set(gt) - set(n for n in names if n))
    extra = sorted(set(n for n in names if n) - set(gt))
    unnamed = sum(1 for n in names if not n)
    duplicated = sorted({n for n in names if n and names.count(n) > 1})
    ok = (
        found == expected_count
        and not missing
        and not extra
        and not unnamed
        and not duplicated
    )
    result.update(
        {
            "status": "pass" if ok else "fail",
            "missing_names": missing,
            "extra_names": extra,
            "unnamed_territories": unnamed,
            "duplicated_names": duplicated,
            "name_evidence": "ground-truth name list",
        }
    )
    return result


def check_c_no_nonplayable(
    territories: list[dict],
    verification: dict | None,
) -> dict[str, Any]:
    """Automated part + explicit human-confirmation gate.

    The automatable evidence is per-territory ``name_source``: a territory
    whose geometry was claimed by a ground-truth seed (or explicitly
    authored by a human) is at least anchored to something playable.  What
    NO automation can decide is whether a claimed mask also swallowed
    scenery, or whether a decoration slipped in under a legitimate name --
    that is the human overlay confirmation.
    """
    unanchored = [
        t["territory_id"]
        for t in territories
        if t.get("name_source") in (None, "", "unknown")
    ]
    conf = (verification or {}).get("overlay_confirmed") or {}
    human_ok = bool(conf.get("verified")) and bool(conf.get("by"))
    if unanchored:
        status = "fail"
    elif human_ok:
        status = "pass"
    else:
        status = "unverified"
    return {
        "criterion": "c: no non-playable regions included",
        "status": status,
        "automated": {
            "territories_without_anchored_name_source": unanchored,
            "note": (
                "this sub-check is necessary, not sufficient: it cannot see "
                "scenery merged into a legitimately-named territory"
            ),
        },
        "human_confirmation": (
            {"confirmed": True, "by": conf.get("by"), "at": conf.get("at")}
            if human_ok
            else {
                "confirmed": False,
                "needs": "a human must view overlay.png and confirm via "
                "annotations.json verification.overlay_confirmed",
            }
        ),
    }


def check_d_bonuses(
    bonuses_doc: dict,
    territory_ids: set[int],
    edges: set[tuple[int, int]] | None,
    verification: dict | None,
) -> dict[str, Any]:
    problems = validate_bonuses(bonuses_doc, territory_ids, edges)
    entries = bonuses_doc.get("bonuses", [])
    needs_review = [i for i, e in enumerate(entries) if e.get("status") == "needs_review"]
    conf = (verification or {}).get("bonuses_confirmed") or {}
    human_ok = bool(conf.get("verified")) and bool(conf.get("by"))
    if problems:
        status = "fail"
    elif human_ok and not needs_review:
        status = "pass"
    else:
        status = "unverified"
    return {
        "criterion": "d: troop-bonus rules accurate",
        "status": status,
        "schema_problems": problems,
        "n_entries": len(entries),
        "entries_needing_review": needs_review,
        "transcription": {
            "note": (
                "no automated ground truth exists for bonuses; values are "
                "only as good as their transcription provenance below"
            ),
            "provenance": bonuses_doc.get("provenance"),
        },
        "human_confirmation": (
            {"confirmed": True, "by": conf.get("by"), "at": conf.get("at")}
            if human_ok
            else {
                "confirmed": False,
                "needs": "a human must check bonuses.json against the artwork "
                "and confirm via annotations.json verification.bonuses_confirmed",
            }
        ),
    }
