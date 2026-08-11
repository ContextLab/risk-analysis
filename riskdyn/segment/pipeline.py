"""Orchestration: artwork -> masks -> territories -> reviewable artifacts.

Per map, writes under ``data/processed/maps/<map_id>/``:
    sam_masks.npz    -- cached raw SAM masks (model + params keyed)
    territories.svg  -- one <path> per territory with id, centroid, area
    territories.json -- per-territory polygons/centroid/area/flags
    report.json      -- confidence report (counts, areas, warnings, bijection,
                        seed provenance, measured anchorless count)
    overlay.png      -- human-review overlay (the pipeline is the ONLY
                        writer of this file in the normal flow)

Mask selection is seed-driven whenever a seed source supplies seeds for the
map (:func:`riskdyn.segment.select.select_masks_by_seeds`); otherwise the
legacy candidate-filter path runs and ``report.json`` says so explicitly.
Seed sources are pluggable: today only the D12 fixture anchors for map 1
exist; a text-detection seed source for the remaining maps plugs into
``DEFAULT_SEED_SOURCES`` when it lands.

Run from the CLI:  ./.venv/bin/python -m riskdyn.segment.pipeline 1 79 100 34
"""
from __future__ import annotations

import argparse
import json
import pathlib
import time
from dataclasses import dataclass
from typing import Callable, Sequence

import numpy as np

from riskdyn.segment import catalog as cat
from riskdyn.segment.candidates import (
    CandidateParams,
    drop_water_labels,
    paint_label_map,
    select_masks,
    split_merged_labels,
)
from riskdyn.segment.geometry import (
    TerritoryShape,
    claim_coastal_margin,
    close_label_gaps,
    extract_territories,
    write_svg,
)
from riskdyn.segment.ground_truth import load_label_points
from riskdyn.segment.loader import load_map_image
from riskdyn.segment.overlay import draw_overlay
from riskdyn.segment.report import bijection_check, build_report
from riskdyn.segment.sam import SamParams, cached_generate
from riskdyn.segment.select import (
    Seed,
    SelectionResult,
    SelectParams,
    build_label_map,
    select_masks_by_seeds,
)

DEFAULT_OUT_ROOT = cat.REPO_ROOT / "data" / "processed" / "maps"
MAP1_FIXTURE = cat.REPO_ROOT / "tests" / "fixtures" / "game_map1_territories.html"


@dataclass(frozen=True)
class SeedSet:
    """Seeds for one map, with provenance."""

    seeds: tuple[Seed, ...]
    source: str      # e.g. "d12-fixture-anchors"; goes into report.json
    kind: str        # SelectParams.seed_kind: "render" | "text"


# A seed source inspects a map id and returns seeds, or None if it has none
# for that map.  Sources are tried in order; first hit wins.
SeedSource = Callable[[int], "SeedSet | None"]


def d12_fixture_seeds(map_id: int) -> SeedSet | None:
    """Render anchors from the D12 ground-truth fixture (map 1 only).

    Image dimensions match catalog dimensions, so the fixture's data-x/y
    coordinates index directly into pixel space -- no registration step.
    """
    if map_id != 1 or not MAP1_FIXTURE.is_file():
        return None
    points = load_label_points(MAP1_FIXTURE)
    return SeedSet(
        seeds=tuple(Seed(p.territory_id, p.x, p.y, p.name) for p in points),
        source="d12-fixture-anchors",
        kind="render",
    )


# Pluggable: the text-detection seed source for the other 76 maps appends
# itself here when that workstream lands (kind="text").
DEFAULT_SEED_SOURCES: tuple[SeedSource, ...] = (d12_fixture_seeds,)


@dataclass
class MapResult:
    map_id: int
    shapes: list[TerritoryShape]
    label_map: np.ndarray
    report: dict
    out_dir: pathlib.Path
    seeded: bool = False
    seed_groups: list[tuple[int, ...]] | None = None  # label k -> seed ids
    selection: SelectionResult | None = None


def _find_seeds(
    map_id: int, seed_sources: Sequence[SeedSource]
) -> SeedSet | None:
    for source in seed_sources:
        seed_set = source(map_id)
        if seed_set is not None and seed_set.seeds:
            return seed_set
    return None


def run_map(
    map_id: int,
    out_root: str | pathlib.Path = DEFAULT_OUT_ROOT,
    sam_params: SamParams | None = None,
    cand_params: CandidateParams | None = None,
    device: str | None = None,
    write_artifacts: bool = True,
    seed_sources: Sequence[SeedSource] | None = None,
) -> MapResult:
    """Run the full stage-1 pipeline on one map.

    ``seed_sources``: tried in order; the first source with seeds for this
    map switches selection to the seed-driven path.  ``None`` means
    ``DEFAULT_SEED_SOURCES``; pass ``()`` to force the unseeded path.
    """
    summaries = cat.load_catalog()
    if map_id not in summaries:
        raise KeyError(f"map {map_id} not in catalog")
    summary = summaries[map_id]

    image = load_map_image(
        cat.image_path(map_id), expected_size=(summary.width, summary.height)
    )
    out_dir = pathlib.Path(out_root) / str(map_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    raw = cached_generate(image, out_dir / "sam_masks.npz", sam_params, device)
    p = cand_params or CandidateParams()

    if seed_sources is None:
        seed_sources = DEFAULT_SEED_SOURCES
    seed_set = _find_seeds(map_id, seed_sources)
    selection: SelectionResult | None = None
    seed_groups: list[tuple[int, ...]] | None = None

    if seed_set is not None:
        # ---- seed-driven path -------------------------------------------
        selection = select_masks_by_seeds(
            raw, list(seed_set.seeds), image,
            SelectParams(seed_kind=seed_set.kind),
        )
        label_map, seed_groups = build_label_map(selection, image.shape[:2])
        label_map = close_label_gaps(
            label_map, image, p.gap_close_px, p.land_claim_px, p.land_color_dist
        )
        warnings = list(selection.warnings) + [
            f"unmatched seed {sid}: {reason}"
            for sid, reason in selection.unmatched
        ]
    else:
        # ---- legacy candidate-filter path (no seeds for this map) -------
        kept, warn_a = select_masks(raw, image.shape[:2], cand_params, image=image)
        label_map, warn_b = paint_label_map(
            kept, image.shape[:2], cand_params, image=image)
        label_map, warn_c = drop_water_labels(label_map, image, cand_params)
        label_map, warn_d = split_merged_labels(label_map, image, cand_params)
        label_map = close_label_gaps(
            label_map, image, p.gap_close_px, p.land_claim_px, p.land_color_dist
        )
        # Final water pass: gap closing can glue printed water text onto
        # small junk labels, tipping their composition to mostly-water;
        # convict them.
        label_map, warn_e = drop_water_labels(label_map, image, cand_params)
        warnings = warn_a + warn_b + warn_c + warn_d + warn_e

    shapes = extract_territories(label_map)

    # The headline bijection is measured on the polygons actually written
    # out (no coastal buffer).  The buffer-assisted figure is a secondary
    # diagnostic for island labels D12 prints in the water; it never touches
    # the emitted polygons.
    labels = None
    bijection = None
    bijection_buffered = None
    if map_id == 1 and MAP1_FIXTURE.is_file():
        labels = load_label_points(MAP1_FIXTURE)
        bijection = bijection_check(shapes, labels)
        buffered_shapes = extract_territories(
            claim_coastal_margin(label_map, p.coastal_buffer_px)
        )
        bijection_buffered = bijection_check(buffered_shapes, labels)

    selection_summary = None
    if selection is not None:
        selection_summary = {
            "n_seeds": len(seed_set.seeds),
            "n_input_masks": selection.n_input_masks,
            "n_pool": selection.n_pool,
            "n_matched_seeds": len(selection.claims),
            "n_selected_groups": len(seed_groups),
            "n_rejected_no_seed": selection.n_rejected_no_seed,
            "unmatched": [
                {"seed_id": sid, "reason": reason}
                for sid, reason in selection.unmatched
            ],
            "merges": [list(g) for g in selection.merges],
        }

    report = build_report(
        map_id,
        summary.name,
        summary.num_territories,
        shapes,
        image.shape[:2],
        warnings,
        bijection,
        bijection_buffered=bijection_buffered,
        coastal_buffer_px=p.coastal_buffer_px,
        gap_close_px=p.gap_close_px,
        land_claim_px=p.land_claim_px,
        seeded=seed_set is not None,
        seed_source=seed_set.source if seed_set is not None else None,
        selection=selection_summary,
        labels=labels,
    )

    if write_artifacts:
        write_svg(shapes, (summary.width, summary.height), out_dir / "territories.svg")
        (out_dir / "territories.json").write_text(
            json.dumps(
                [
                    {
                        "index": s.index,
                        # back-compat single-polygon view (largest component)
                        "polygon": [[round(x, 1), round(y, 1)] for x, y in s.polygon],
                        # all components, largest first
                        "polygons": [
                            [[round(x, 1), round(y, 1)] for x, y in poly]
                            for poly in s.polygons
                        ],
                        "centroid": [round(s.centroid[0], 1), round(s.centroid[1], 1)],
                        "area_px": s.area_px,
                        "flags": list(s.flags),
                    }
                    for s in shapes
                ],
                indent=1,
            )
        )
        (out_dir / "report.json").write_text(json.dumps(report, indent=1))
        seed_tag = (
            f"seeded:{seed_set.source}" if seed_set is not None else "UNSEEDED"
        )
        header = (
            f"map {map_id} {summary.name} [{seed_tag}]: {len(shapes)} found / "
            f"{summary.num_territories} expected"
            + (
                f" | bijection {bijection['n_bijective']}/{bijection['n_labels']}"
                f" (buffered {bijection_buffered['n_bijective']}"
                f"/{bijection_buffered['n_labels']})"
                if bijection
                else ""
            )
        )
        # Single writer: overlay.png is written HERE and nowhere else in the
        # normal flow (select.py's debug CLI also can, but is not part of it).
        draw_overlay(image, shapes, out_dir / "overlay.png", None, header)

    return MapResult(
        map_id, shapes, label_map, report, out_dir,
        seeded=seed_set is not None,
        seed_groups=seed_groups,
        selection=selection,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="stage-1 map segmentation")
    ap.add_argument("map_ids", nargs="*", type=int, help="map ids; empty = all")
    ap.add_argument("--out-root", type=pathlib.Path, default=DEFAULT_OUT_ROOT)
    ap.add_argument("--device", default=None)
    args = ap.parse_args(argv)

    ids = args.map_ids or sorted(cat.load_catalog())
    for map_id in ids:
        t0 = time.time()
        try:
            result = run_map(map_id, args.out_root, device=args.device)
        except Exception as e:  # keep going over the corpus; report at the end
            print(f"map {map_id:>3}  FAILED: {e}")
            continue
        r = result.report
        tag = (
            f"seeded:{r['seeding']['seed_source']}"
            if r["seeding"]["seeded"]
            else "UNSEEDED"
        )
        print(
            f"map {map_id:>3}  {r['segmented_territories']:>3}/{r['expected_territories']:<3}"
            f"  {time.time() - t0:6.1f}s  warnings={len(r['warnings'])}  [{tag}]"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
