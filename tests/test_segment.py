"""Stage-1 segmentation tests (issue #4).

These are real tests: they load the actual downloaded artwork and, for the
SAM-backed ones, run the actual model.  The first run downloads the
facebook/sam-vit-base weights; raw masks are cached under
``data/processed/maps/<id>/sam_masks.npz`` so repeat runs are cheap.  The
determinism test deliberately bypasses that cache.
"""
from __future__ import annotations

import pathlib

import numpy as np
import pytest

from riskdyn.segment.catalog import image_path, load_catalog
from riskdyn.segment.ground_truth import load_label_points
from riskdyn.segment.loader import load_map_image, sniff_format

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "game_map1_territories.html"


# ---------------------------------------------------------------- fast tests

def test_ground_truth_has_42_labels():
    pts = load_label_points(FIXTURE)
    assert len(pts) == 42
    assert len({p.territory_id for p in pts}) == 42
    names = {p.name for p in pts}
    assert "Northwest Territory" in names and "Kamchatka" in names
    cat = load_catalog()
    for p in pts:
        assert 0 <= p.x < cat[1].width
        assert 0 <= p.y < cat[1].height


def test_catalog_loads_offline():
    cat = load_catalog()
    assert len(cat) == 77
    assert cat[1].num_territories == 42
    assert cat[100].num_territories == 150


def test_map30_is_png_with_alpha_and_loads():
    # Map 30 ("Tor") is a PNG with an alpha channel despite the .jpg name.
    path = image_path(30)
    assert sniff_format(path) == "PNG"
    from PIL import Image

    with Image.open(path) as im:
        assert im.mode == "RGBA"
    cat = load_catalog()
    arr = load_map_image(path, (cat[30].width, cat[30].height))
    assert arr.shape == (cat[30].height, cat[30].width, 3)
    assert arr.dtype == np.uint8


def test_dimension_mismatch_raises():
    with pytest.raises(ValueError, match="catalog size"):
        load_map_image(image_path(1), expected_size=(100, 100))


def test_all_downloaded_images_match_catalog_dimensions():
    cat = load_catalog()
    for map_id, summary in cat.items():
        path = image_path(map_id)
        if not path.is_file():
            pytest.fail(f"missing artwork for map {map_id}")
        from PIL import Image

        with Image.open(path) as im:
            assert im.size == (summary.width, summary.height), (
                f"map {map_id}: image {im.size} != catalog "
                f"{(summary.width, summary.height)}"
            )


# ---------------------------------------------------------- SAM-backed tests

@pytest.fixture(scope="module")
def map1_result():
    from riskdyn.segment.pipeline import run_map

    return run_map(1)


def test_map1_bijection(map1_result):
    """The stage-1 gate: 42 label points <-> 42 distinct polygons."""
    bij = map1_result.report["bijection"]
    assert bij["n_labels"] == 42
    assert bij["n_bijective"] == 42, (
        f"bijection {bij['n_bijective']}/42; failures: {bij['failures']}"
    )


def test_map1_ocean_is_not_a_territory(map1_result):
    """Open-ocean points must fall inside no territory polygon."""
    from riskdyn.segment.geometry import polygons_containing

    # Mid-Atlantic, south of Africa, and bottom-left open water on map 1.
    ocean_points = [(350, 500), (500, 640), (60, 640)]
    for x, y in ocean_points:
        hits = polygons_containing(map1_result.shapes, x, y)
        assert hits == [], f"ocean point ({x},{y}) inside territories {hits}"
    # The label map agrees: those pixels are background.
    for x, y in ocean_points:
        assert map1_result.label_map[y, x] == 0


def test_map1_artifacts_written(map1_result):
    out = map1_result.out_dir
    svg = (out / "territories.svg").read_text()
    assert svg.count("<path") == len(map1_result.shapes)
    assert 'id="territory-1"' in svg
    assert (out / "overlay.png").is_file()
    assert (out / "report.json").is_file()
    assert (out / "territories.json").is_file()


def test_same_image_same_output_across_runs():
    """Determinism: two independent SAM runs give identical territories."""
    from riskdyn.segment.candidates import paint_label_map, select_masks
    from riskdyn.segment.geometry import extract_territories
    from riskdyn.segment.sam import SamMaskGenerator

    cat = load_catalog()
    image = load_map_image(image_path(1), (cat[1].width, cat[1].height))

    runs = []
    for _ in range(2):
        raw = SamMaskGenerator().generate(image)  # no cache: real re-run
        kept, _ = select_masks(raw, image.shape[:2])
        label_map, _ = paint_label_map(kept, image.shape[:2])
        runs.append((raw, extract_territories(label_map)))

    raw_a, shapes_a = runs[0]
    raw_b, shapes_b = runs[1]
    assert len(raw_a) == len(raw_b)
    for a, b in zip(raw_a, raw_b):
        assert np.array_equal(a.mask, b.mask)
    assert len(shapes_a) == len(shapes_b)
    for a, b in zip(shapes_a, shapes_b):
        assert a.index == b.index
        assert a.polygon == b.polygon
        assert a.centroid == b.centroid
        assert a.area_px == b.area_px
