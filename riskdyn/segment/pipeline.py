"""Orchestration: artwork -> masks -> territories -> reviewable artifacts.

Per map, writes under ``data/processed/maps/<map_id>/``:
    sam_masks.npz    -- cached raw SAM masks (model + params keyed)
    territories.svg  -- one <path> per territory with id, centroid, area
    territories.json -- per-territory polygon/centroid/area/flags
    report.json      -- confidence report (counts, areas, warnings, bijection)
    overlay.png      -- human-review overlay

Run from the CLI:  ./.venv/bin/python -m riskdyn.segment.pipeline 1 79 100 34
"""
from __future__ import annotations

import argparse
import json
import pathlib
import time
from dataclasses import dataclass

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

DEFAULT_OUT_ROOT = cat.REPO_ROOT / "data" / "processed" / "maps"
MAP1_FIXTURE = cat.REPO_ROOT / "tests" / "fixtures" / "game_map1_territories.html"


@dataclass
class MapResult:
    map_id: int
    shapes: list[TerritoryShape]
    label_map: np.ndarray
    report: dict
    out_dir: pathlib.Path


def run_map(
    map_id: int,
    out_root: str | pathlib.Path = DEFAULT_OUT_ROOT,
    sam_params: SamParams | None = None,
    cand_params: CandidateParams | None = None,
    device: str | None = None,
    write_artifacts: bool = True,
) -> MapResult:
    """Run the full stage-1 pipeline on one map."""
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
    kept, warn_a = select_masks(raw, image.shape[:2], cand_params, image=image)
    label_map, warn_b = paint_label_map(kept, image.shape[:2], cand_params, image=image)
    label_map, warn_c = drop_water_labels(label_map, image, cand_params)
    label_map, warn_d = split_merged_labels(label_map, image, cand_params)
    label_map = close_label_gaps(
        label_map, image, p.gap_close_px, p.land_claim_px, p.land_color_dist
    )
    # Final water pass: gap closing can glue printed water text onto small
    # junk labels, tipping their composition to mostly-water; convict them.
    label_map, warn_e = drop_water_labels(label_map, image, cand_params)
    shapes = extract_territories(label_map)

    # The headline bijection is measured on the polygons actually written
    # out (no coastal buffer).  The buffer-assisted figure is a secondary
    # diagnostic for island labels D12 prints in the water; it never touches
    # the emitted polygons.
    bijection = None
    bijection_buffered = None
    if map_id == 1 and MAP1_FIXTURE.is_file():
        labels = load_label_points(MAP1_FIXTURE)
        bijection = bijection_check(shapes, labels)
        buffered_shapes = extract_territories(
            claim_coastal_margin(label_map, p.coastal_buffer_px)
        )
        bijection_buffered = bijection_check(buffered_shapes, labels)

    report = build_report(
        map_id,
        summary.name,
        summary.num_territories,
        shapes,
        image.shape[:2],
        warn_a + warn_b + warn_c + warn_d + warn_e,
        bijection,
        bijection_buffered=bijection_buffered,
        coastal_buffer_px=p.coastal_buffer_px,
        gap_close_px=p.gap_close_px,
        land_claim_px=p.land_claim_px,
    )

    if write_artifacts:
        write_svg(shapes, (summary.width, summary.height), out_dir / "territories.svg")
        (out_dir / "territories.json").write_text(
            json.dumps(
                [
                    {
                        "index": s.index,
                        "polygon": [[round(x, 1), round(y, 1)] for x, y in s.polygon],
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
        header = (
            f"map {map_id} {summary.name}: {len(shapes)} found / "
            f"{summary.num_territories} expected"
            + (
                f" | bijection {bijection['n_bijective']}/{bijection['n_labels']}"
                f" (buffered {bijection_buffered['n_bijective']}"
                f"/{bijection_buffered['n_labels']})"
                if bijection
                else ""
            )
        )
        draw_overlay(image, shapes, out_dir / "overlay.png", None, header)

    return MapResult(map_id, shapes, label_map, report, out_dir)


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
        print(
            f"map {map_id:>3}  {r['segmented_territories']:>3}/{r['expected_territories']:<3}"
            f"  {time.time() - t0:6.1f}s  warnings={len(r['warnings'])}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
