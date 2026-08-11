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


# Label anchors that can fail the buffer=0 bijection, each diagnosed by
# hand (2026-08-10, re-verified after the red-team rework):
#   Greenland(11)/Iceland(32)  -- D12 renders Iceland's label anchor on top
#       of Greenland's landmass (verified by zoomed crop), so faithful
#       geometry can never give the anchor its own polygon;
#   S. Africa(41)/Madagascar(42) -- Madagascar's anchor is printed in the
#       Mozambique Channel 3.8 px off the MAINLAND coast (which really
#       extends to x~603 at that row) and ~15 px from the island, so
#       nearest-label gap closing merges it into South Africa's polygon;
#   Mongolia(56) -- D12 prints the anchor 1.0 px from Irkutsk's mask and
#       1.4 px from Mongolia's, on the shared drawn border stroke; the
#       contested-pixel rule leaves it unclaimed rather than guessing
#       (both polygons themselves are correct);
#   Britain(43)/Indonesia(61)/W. Australia(63)/Alaska(66) -- island/edge
#       labels D12 prints in open water (13.8/11.2/4.1/~20 px offshore);
#       their polygons exist and are correct; only the buffered secondary
#       matching can associate these anchors.
KNOWN_UNMATCHED_B0 = {11, 32, 41, 42, 43, 56, 61, 63, 66}


def test_map1_bijection(map1_result):
    """The stage-1 gate, measured HONESTLY: no coastal buffer.

    The headline bijection is computed on the polygons actually written
    out.  Floors, not pins: improvement never fails the suite, regression
    does.  Any newly-failing anchor outside the characterized set above
    also fails loudly.
    """
    bij = map1_result.report["bijection"]
    assert bij["n_labels"] == 42
    assert bij["n_in_exactly_one"] >= 37, bij["failures"]
    assert bij["n_bijective"] >= 33, bij["failures"]
    failed_ids = {f["territory_id"] for f in bij["failures"]}
    assert failed_ids <= KNOWN_UNMATCHED_B0, bij["failures"]


def test_map1_bijection_buffered_secondary(map1_result):
    """Buffer-assisted matching (secondary diagnostic, never the headline).

    With near-shore water claimed to the nearest territory for LABEL
    MATCHING ONLY (the emitted polygons are untouched), offshore island
    labels resolve.  This floor preserves the strength of the old pinned
    36/42 figure.
    """
    bb = map1_result.report["bijection_buffered"]
    assert bb["buffer_px"] > 0
    assert bb["n_labels"] == 42
    assert bb["n_in_exactly_one"] >= 42
    assert bb["n_bijective"] >= 36, bb["failures"]


def test_map1_polygon_count_sane(map1_result):
    """Polygon count within a stated factor of the catalog's territory
    count.  num_territories is a warning signal, never a filter, but a
    count exploding past this factor means the mask soup leaked through."""
    n = len(map1_result.shapes)
    expected = map1_result.report["expected_territories"]
    assert expected == 42
    assert n >= expected, f"only {n} polygons for {expected} territories"
    assert n <= 2.5 * expected, f"{n} polygons for {expected} territories"


def test_map1_junk_fraction_bounded(map1_result):
    """The fraction of polygons containing no ground-truth anchor stays
    below a stated threshold (junk: title letters, name pills, fragments;
    also counts real-but-unlabelled decorative islands)."""
    from riskdyn.segment.geometry import polygons_containing

    pts = load_label_points(FIXTURE)
    anchored = set()
    for p in pts:
        for hit in polygons_containing(map1_result.shapes, p.x, p.y):
            anchored.add(hit)
    n = len(map1_result.shapes)
    frac_anchorless = 1.0 - len(anchored) / n
    assert frac_anchorless <= 0.70, (
        f"{n - len(anchored)}/{n} polygons contain no anchor"
    )


def test_map1_ocean_is_not_a_territory(map1_result):
    """No polygon may be made of water-coloured pixels.

    The real form of the check: for EVERY emitted label, the fraction of
    its pixels within RGB distance 35 of the map's background colour must
    stay below the conviction threshold -- not just at a few hand-picked
    probe points.  (The probes are kept as a cheap sanity layer.)
    """
    import numpy as np

    from riskdyn.segment.geometry import polygons_containing

    cat = load_catalog()
    image = load_map_image(image_path(1), (cat[1].width, cat[1].height))
    label_map = map1_result.label_map
    bg_color = np.median(image[label_map == 0], axis=0)
    for label in range(1, int(label_map.max()) + 1):
        m = label_map == label
        if not m.any():
            continue
        dpix = np.linalg.norm(image[m].astype(np.float32) - bg_color, axis=1)
        water_frac = float((dpix < 35).mean())
        assert water_frac <= 0.60, (
            f"label {label} is {water_frac:.0%} water-coloured pixels"
        )
    # Cheap probe layer: mid-Atlantic, south of Africa, bottom-left water.
    ocean_points = [(350, 500), (500, 640), (60, 640)]
    for x, y in ocean_points:
        hits = polygons_containing(map1_result.shapes, x, y)
        assert hits == [], f"ocean point ({x},{y}) inside territories {hits}"
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
    from riskdyn.segment.candidates import (
        CandidateParams,
        drop_water_labels,
        paint_label_map,
        select_masks,
        split_merged_labels,
    )
    from riskdyn.segment.geometry import close_label_gaps, extract_territories
    from riskdyn.segment.sam import SamMaskGenerator

    cat = load_catalog()
    image = load_map_image(image_path(1), (cat[1].width, cat[1].height))

    p = CandidateParams()
    runs = []
    for _ in range(2):
        raw = SamMaskGenerator().generate(image)  # no cache: real re-run
        kept, _ = select_masks(raw, image.shape[:2], image=image)
        label_map, _ = paint_label_map(kept, image.shape[:2], image=image)
        label_map, _ = drop_water_labels(label_map, image)
        label_map, _ = split_merged_labels(label_map, image)
        label_map = close_label_gaps(
            label_map, image, p.gap_close_px, p.land_claim_px, p.land_color_dist
        )
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
