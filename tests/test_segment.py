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
    """Seeded run: map 1 has D12 fixture anchors, so the default seed
    sources switch run_map onto the seed-driven selection path."""
    from riskdyn.segment.pipeline import run_map

    return run_map(1)


@pytest.fixture(scope="module")
def map1_unseeded(map1_result, tmp_path_factory):
    """The same map forced down the legacy (unseeded) path, into its own
    out dir.  Reuses the cached SAM masks so no model re-run is needed."""
    import shutil

    from riskdyn.segment.pipeline import run_map

    out_root = tmp_path_factory.mktemp("unseeded")
    (out_root / "1").mkdir()
    shutil.copy(map1_result.out_dir / "sam_masks.npz",
                out_root / "1" / "sam_masks.npz")
    return run_map(1, out_root=out_root, seed_sources=())


# Label anchors that can fail the buffer=0 bijection, each diagnosed by
# hand (2026-08-10, re-verified after the red-team rework and again on
# 2026-08-11 after wiring seed-driven selection into the pipeline):
#   Greenland(11)/Iceland(32)  -- D12 renders Iceland's label anchor on top
#       of Greenland's landmass (verified by zoomed crop), so faithful
#       geometry can never give the anchor its own polygon; seed-driven
#       selection reports the pair as an UNRESOLVED MERGE (one label);
#   S. Africa(41)/Madagascar(42) -- Madagascar's anchor is printed in the
#       Mozambique Channel 3.8 px off the MAINLAND coast (which really
#       extends to x~603 at that row) and ~15 px from the island, so
#       nearest-label gap closing merges it into South Africa's polygon;
#   Scandinavia(44) -- the anchor sits on coastal text 10.6 px off
#       Scandinavia's own mask, nearer to Northern Europe's; the legacy
#       path's accidental halo-mask happened to cover it, the faithful
#       seed-claimed mask does not (diagnosed in the select.py session);
#   Mongolia(56) -- D12 prints the anchor 1.0 px from Irkutsk's mask and
#       1.4 px from Mongolia's, on the shared drawn border stroke; the
#       contested-pixel rule leaves it unclaimed rather than guessing
#       (both polygons themselves are correct);
#   Britain(43)/Indonesia(61)/Alaska(66) -- island/edge labels D12 prints
#       in open water (13.8/11.2/~20 px offshore); their polygons exist and
#       are correct; only the buffered secondary matching can associate
#       these anchors.
#   (W. Australia(63), in the legacy set, is FIXED by seed-driven
#   selection and deliberately NOT allowed to regress silently.)
KNOWN_UNMATCHED_B0 = {11, 32, 41, 42, 43, 44, 56, 61, 66}

# Buffered (secondary) matching under seed-driven selection: every failure
# is one of these characterized pairs, where two anchors land in one
# polygon -- the merge pair (11/32), the channel pair (41/42), the coastal
# text pair (44/46), and the contested-stroke pair (55/56).
KNOWN_BUFFERED_PAIRS = {11, 32, 41, 42, 44, 46, 55, 56}


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
    labels resolve.  Measured 2026-08-11 on the seed-driven pipeline:
    34/42, down from the legacy path's 36/42 by exactly one characterized
    pair (Scandinavia/N. Europe, 44/46: the anchor sits on coastal text
    10.6 px off Scandinavia's mask, which the legacy halo-mask covered by
    accident).  The failure SET is now constrained too -- any new failure
    outside the characterized pairs fails loudly, which the old floor-only
    test could not do.  The legacy path's 36/42 strength is preserved
    verbatim by test_unseeded_legacy_floors.
    """
    bb = map1_result.report["bijection_buffered"]
    assert bb["buffer_px"] > 0
    assert bb["n_labels"] == 42
    assert bb["n_in_exactly_one"] >= 42
    assert bb["n_bijective"] >= 34, bb["failures"]
    failed_ids = {f["territory_id"] for f in bb["failures"]}
    assert failed_ids <= KNOWN_BUFFERED_PAIRS, bb["failures"]


def test_map1_polygon_count_sane(map1_result):
    """Territory count anchored to the catalog, not pinned to today's run.

    Seed-driven selection can only fall short of the expected count via a
    loudly-reported unresolved merge (each merge fuses two seeds into one
    label), so the lower bound is expected minus the reported merges --
    never a silent shortfall.  The upper bound is far below the legacy
    path's 85: the mask soup can no longer leak through."""
    n = len(map1_result.shapes)
    expected = map1_result.report["expected_territories"]
    assert expected == 42
    assert map1_result.report["seeding"]["seeded"] is True
    n_merges = len(map1_result.report["selection"]["merges"])
    assert n >= expected - n_merges, (
        f"only {n} territories for {expected} expected with "
        f"{n_merges} reported merges"
    )
    assert n <= 1.25 * expected, f"{n} territories for {expected} expected"


def test_map1_junk_fraction_bounded(map1_result):
    """The fraction of territories containing no ground-truth anchor stays
    below a stated threshold (junk: title letters, name pills, fragments;
    also counts real-but-unlabelled decorative islands).  Tightened from
    the legacy path's 0.70 -- seed-driven selection measured 6/41 = 0.15.
    Also cross-checks that report.json's anchorless block is the same
    measurement, not a restated definition."""
    from riskdyn.segment.geometry import polygons_containing

    pts = load_label_points(FIXTURE)
    anchored = set()
    for p in pts:
        for hit in polygons_containing(map1_result.shapes, p.x, p.y):
            anchored.add(hit)
    n = len(map1_result.shapes)
    n_anchorless = n - len(anchored)
    assert n_anchorless / n <= 0.25, (
        f"{n_anchorless}/{n} territories contain no anchor"
    )
    reported = map1_result.report["anchorless"]
    assert reported["measured"] is True
    assert reported["n_anchorless_territories"] == n_anchorless
    assert reported["n_territories"] == n


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
    import json

    out = map1_result.out_dir
    svg = (out / "territories.svg").read_text()
    # one <path> per territory; multi-component territories are a single
    # path with several "M ... Z" subpaths, declared via data-n-polygons
    assert svg.count("<path") == len(map1_result.shapes)
    assert 'id="territory-1"' in svg
    assert 'data-n-polygons=' in svg
    assert (out / "overlay.png").is_file()
    assert (out / "report.json").is_file()
    terr = json.loads((out / "territories.json").read_text())
    assert len(terr) == len(map1_result.shapes)
    for entry, shape in zip(terr, map1_result.shapes):
        assert len(entry["polygons"]) == len(shape.polygons)
        assert entry["polygon"] == entry["polygons"][0]  # back-compat view
    # the artifacts on disk are the SEEDED result, and say so
    on_disk = json.loads((out / "report.json").read_text())
    assert on_disk["seeding"]["seeded"] is True
    assert on_disk["seeding"]["seed_source"] == "d12-fixture-anchors"


def test_map1_report_is_seeded_and_measured(map1_result):
    """The report must not overstate: seeded provenance is explicit, the
    anchorless count is a measurement (with the territories it names), and
    the buffer=0 bijection is present as the headline."""
    r = map1_result.report
    assert r["seeding"] == {
        "seeded": True, "seed_source": "d12-fixture-anchors"
    }
    a = r["anchorless"]
    assert a["measured"] is True
    assert a["n_anchorless_territories"] == len(
        a["anchorless_territory_indices"]
    )
    assert 0 <= a["n_anchorless_territories"] <= 8
    assert r["n_polygons"] == sum(len(s.polygons) for s in map1_result.shapes)
    # archipelagos exist on this map: polygons must exceed territories
    assert r["n_polygons"] >= r["segmented_territories"] + 4
    assert "bijection" in r and r["bijection"]["n_labels"] == 42
    assert r["selection"]["n_seeds"] == 42


def test_map1_multipolygon_territories_preserved(map1_result):
    """Archipelago territories keep ALL their significant components.

    Indonesia (seed 61) specifically: its label spans several islands, and
    the formerly-dropped 512 px island must land in the SAME territory as
    the main landmass.  Checked from the label map (which components exist)
    through to the emitted polygons (which components were written out)."""
    import cv2

    from riskdyn.segment.geometry import (
        MIN_COMPONENT_FRAC,
        polygons_containing,
    )

    assert map1_result.seed_groups is not None
    label = next(
        k for k, g in enumerate(map1_result.seed_groups, start=1) if 61 in g
    )
    mask = (map1_result.label_map == label).astype(np.uint8)
    h, w = mask.shape
    min_area = max(1, round(MIN_COMPONENT_FRAC * h * w))
    n_comp, comp = cv2.connectedComponents(mask, connectivity=4)
    sizes = sorted(
        (int((comp == c).sum()), c) for c in range(1, n_comp)
    )[::-1]
    significant = [(a, c) for a, c in sizes if a >= min_area]
    assert len(significant) >= 2, "Indonesia should be an archipelago"

    territory_hits = []
    for _, c in significant[:2]:
        # deep-interior pixel of the component, robust to outline
        # simplification at the edges
        dist = cv2.distanceTransform(
            (comp == c).astype(np.uint8), cv2.DIST_L2, 3
        )
        y, x = np.unravel_index(int(dist.argmax()), dist.shape)
        hits = polygons_containing(map1_result.shapes, int(x), int(y))
        assert hits, f"component at ({x},{y}) fell out of every polygon"
        territory_hits.append(set(hits))
    common = set.intersection(*territory_hits)
    assert common, (
        "Indonesia's islands were emitted under different territories: "
        f"{territory_hits}"
    )
    # and its second island is genuinely significant, not a sliver
    assert significant[1][0] >= 300

    # map-wide: several territories are multi-polygon (~6 on map 1)
    n_multi = sum(1 for s in map1_result.shapes if len(s.polygons) >= 2)
    assert n_multi >= 3, (
        f"only {n_multi} multi-polygon territories; components are being "
        "dropped again"
    )


def test_unseeded_run_is_labelled_as_such(map1_unseeded):
    """An unseeded map's artifacts can never be mistaken for seeded ones."""
    import json

    r = map1_unseeded.report
    assert map1_unseeded.seeded is False
    assert r["seeding"]["seeded"] is False
    assert r["seeding"]["seed_source"] is None
    assert "NOT seed-driven" in r["seeding"]["note"]
    assert "selection" not in r
    on_disk = json.loads(
        (map1_unseeded.out_dir / "report.json").read_text()
    )
    assert on_disk["seeding"]["seeded"] is False
    assert "NOT seed-driven" in on_disk["seeding"]["note"]
    assert (map1_unseeded.out_dir / "overlay.png").is_file()
    assert (map1_unseeded.out_dir / "territories.svg").is_file()


def test_unseeded_legacy_floors(map1_unseeded):
    """The legacy path still serves the 76 seedless maps, so its old
    guarantees are preserved verbatim (the floors the seeded tests above
    adapted): bijection 33/42 at buffer=0 within the legacy characterized
    set, and 36/42 buffered."""
    bij = map1_unseeded.report["bijection"]
    assert bij["n_labels"] == 42
    assert bij["n_in_exactly_one"] >= 37, bij["failures"]
    assert bij["n_bijective"] >= 33, bij["failures"]
    failed = {f["territory_id"] for f in bij["failures"]}
    assert failed <= {11, 32, 41, 42, 43, 56, 61, 63, 66}, bij["failures"]
    bb = map1_unseeded.report["bijection_buffered"]
    assert bb["n_in_exactly_one"] >= 42
    assert bb["n_bijective"] >= 36, bb["failures"]
    # anchorless is MEASURED here too -- the legacy path's honest 50/85
    a = map1_unseeded.report["anchorless"]
    assert a["measured"] is True
    assert a["n_anchorless_territories"] > 0  # legacy soup, honestly reported


def test_overlay_written_exactly_once(map1_result, tmp_path, monkeypatch):
    """One run -> exactly one overlay.png write, by the pipeline alone.

    The overlay used to be written by both select.py's CLI and the
    pipeline, with the last writer contradicting select_report.json.  The
    wrapper below counts real draw_overlay calls (the real function still
    runs and writes)."""
    import shutil

    from riskdyn.segment import pipeline as pl

    (tmp_path / "1").mkdir()
    shutil.copy(map1_result.out_dir / "sam_masks.npz",
                tmp_path / "1" / "sam_masks.npz")
    calls = []
    real = pl.draw_overlay

    def counting(*args, **kwargs):
        calls.append(args[2])  # the output path
        return real(*args, **kwargs)

    monkeypatch.setattr(pl, "draw_overlay", counting)
    res = pl.run_map(1, out_root=tmp_path)
    assert len(calls) == 1
    assert pathlib.Path(calls[0]) == res.out_dir / "overlay.png"
    assert (res.out_dir / "overlay.png").is_file()
    # the pipeline writes exactly its five artifacts -- no select_report
    assert sorted(f.name for f in res.out_dir.iterdir()) == [
        "overlay.png", "report.json", "sam_masks.npz",
        "territories.json", "territories.svg",
    ]


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
