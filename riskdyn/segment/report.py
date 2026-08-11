"""Per-map confidence report, including the World Classic bijection check."""
from __future__ import annotations

import numpy as np

from riskdyn.segment.geometry import TerritoryShape, polygons_containing
from riskdyn.segment.ground_truth import LabelPoint


def bijection_check(
    shapes: list[TerritoryShape], labels: list[LabelPoint]
) -> dict:
    """Check that label points -> territories is a bijection.

    Returns a dict with:
        n_labels: number of ground-truth points
        n_in_exactly_one: labels inside exactly one polygon
        n_bijective: labels that map uniquely to a polygon no other label claims
        failures: per-label diagnosis for everything not bijective
    """
    assignments: dict[int, list[int]] = {}
    for p in labels:
        assignments[p.territory_id] = polygons_containing(shapes, p.x, p.y)

    claimed: dict[int, list[int]] = {}
    for tid, hits in assignments.items():
        if len(hits) == 1:
            claimed.setdefault(hits[0], []).append(tid)

    failures = []
    n_unique = 0
    n_bijective = 0
    for p in labels:
        hits = assignments[p.territory_id]
        if len(hits) == 0:
            failures.append(
                {"territory_id": p.territory_id, "name": p.name,
                 "reason": "label point in no polygon (territory missed or merged into background)"}
            )
        elif len(hits) > 1:
            failures.append(
                {"territory_id": p.territory_id, "name": p.name,
                 "reason": f"label point inside {len(hits)} polygons: {hits}"}
            )
        else:
            n_unique += 1
            poly = hits[0]
            if len(claimed[poly]) == 1:
                n_bijective += 1
            else:
                others = [t for t in claimed[poly] if t != p.territory_id]
                failures.append(
                    {"territory_id": p.territory_id, "name": p.name,
                     "reason": f"polygon {poly} also claimed by territories {others} (under-segmentation)"}
                )
    return {
        "n_labels": len(labels),
        "n_in_exactly_one": n_unique,
        "n_bijective": n_bijective,
        "failures": failures,
    }


def build_report(
    map_id: int,
    map_name: str,
    expected_territories: int,
    shapes: list[TerritoryShape],
    image_shape: tuple[int, int],
    pipeline_warnings: list[str],
    bijection: dict | None = None,
) -> dict:
    """Assemble the per-map confidence report (JSON-serializable)."""
    areas = np.array([s.area_px for s in shapes], dtype=float)
    image_area = float(image_shape[0] * image_shape[1])
    warnings = list(pipeline_warnings)

    n = len(shapes)
    if n != expected_territories:
        warnings.append(
            f"segmented {n} territories but catalog says {expected_territories}"
        )
    if n and areas.max() > 0.2 * image_area:
        warnings.append("largest territory exceeds 20% of the image; possible ocean/region leak")
    flagged = {s.index: ",".join(s.flags) for s in shapes if s.flags}
    if flagged:
        warnings.append(f"territories flagged during extraction: {sorted(flagged)}")

    report = {
        "map_id": map_id,
        "map_name": map_name,
        "expected_territories": expected_territories,
        "segmented_territories": n,
        "area_px": {
            "min": int(areas.min()) if n else 0,
            "median": float(np.median(areas)) if n else 0.0,
            "max": int(areas.max()) if n else 0,
            "total_frac_of_image": float(areas.sum() / image_area) if n else 0.0,
        },
        "warnings": warnings,
    }
    if bijection is not None:
        report["bijection"] = bijection
        if bijection["n_bijective"] < bijection["n_labels"]:
            report["warnings"].append(
                f"bijection: only {bijection['n_bijective']}/{bijection['n_labels']} labels map uniquely"
            )
    return report
