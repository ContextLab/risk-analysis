"""Colour-sample region proposals (riskdyn.workbench.regions), real data only.

Every test reads the real authored annotations and the real D12 artwork in
data/raw/map_images/.  Map 1's six World Classic continents -- with their
territory_names lists in data/authored/maps/1/annotations.json -- are real
ground truth for the clustering.
"""
import json
import pathlib
import shutil

import numpy as np
import pytest
from PIL import Image

from riskdyn.segment import catalog as cat
from riskdyn.workbench.graph_build import validate_annotations
from riskdyn.workbench.regions import (
    RELIABLE_MIN_FRACTION,
    WriteRefused,
    run_map,
    sample_label_point,
    sample_territories,
)

REPO = pathlib.Path(__file__).resolve().parents[1]
AUTHORED = REPO / "data" / "authored" / "maps"


def _load_image(map_id: int) -> np.ndarray:
    with Image.open(cat.image_path(map_id)) as im:
        return np.asarray(im.convert("RGB"))


def _copy_annotations(map_id: int, tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "authored"
    (root / str(map_id)).mkdir(parents=True)
    shutil.copy(
        AUTHORED / str(map_id) / "annotations.json",
        root / str(map_id) / "annotations.json",
    )
    return root


def test_map1_samples_all_42_and_recovers_the_six_continents(tmp_path):
    report = run_map(1, out_root=tmp_path / "out")
    assert len(report["territories"]) == 42
    assert report["counts"]["n_territories"] == 42

    # The six known World Classic continents are real ground truth: the
    # colour clusters must reproduce their membership exactly.
    doc = json.loads((AUTHORED / "1" / "annotations.json").read_text())
    truth = {
        frozenset(r["territory_names"]): r["name"] for r in doc["regions"]
    }
    assert len(truth) == 6
    found = {
        frozenset(c["territory_names"]) for c in report["clusters"]
    }
    assert found == set(truth), (
        "colour clusters do not match the six continents: "
        f"unmatched truth={[truth[m] for m in set(truth) - found]}"
    )
    assert report["counts"]["n_clusters"] == 6
    assert report["counts"]["catalog_num_regions"] == 6
    assert report["counts"]["clusters_match_catalog"] is True
    # the derived threshold is reported along with how it was chosen
    assert report["threshold"]["value"] > 0
    assert "silhouette" in report["threshold"]["method"]
    # artifacts exist
    assert (tmp_path / "out" / "1" / "region_sample.json").is_file()
    assert (tmp_path / "out" / "1" / "region_overlay.png").is_file()


def test_node_on_black_border_stroke_is_flagged_unreliable():
    """Raw node (594, 97) puts the sample point (609, 107) exactly on the
    black border stroke between Ukraine and Ural on map 1 (verified against
    the artwork): the patch straddles slate-blue, black and green, so the
    consistency fraction collapses and the sample must be flagged."""
    image = _load_image(1)
    _, fraction, (sx, sy) = sample_label_point(image, 594, 97)
    assert (sx, sy) == (609, 107)
    assert fraction < RELIABLE_MIN_FRACTION

    [sample] = sample_territories(
        image, [{"territory_id": 999, "name": "on-border", "x": 594, "y": 97}]
    )
    assert sample.reliable is False
    assert sample.patch_consistency == round(fraction, 3)

    # ... and a node solidly inside a territory is NOT flagged (Quebec).
    _, inside_fraction, _ = sample_label_point(image, 211, 135)
    assert inside_fraction >= RELIABLE_MIN_FRACTION


def test_write_refuses_when_regions_came_from_a_vision_legend_read(tmp_path):
    """Map 1's regions were authored by a vision read of the artwork legend
    (source is not 'colour-sample'), so --write must refuse without --force
    and must leave the file byte-identical."""
    authored = _copy_annotations(1, tmp_path)
    ann = authored / "1" / "annotations.json"
    before = ann.read_text()
    with pytest.raises(WriteRefused, match="vision legend read|colour-sample"):
        run_map(1, write=True, authored_root=authored, out_root=tmp_path / "out")
    assert ann.read_text() == before


def test_write_produces_list_region_ids_and_valid_annotations(tmp_path):
    """Map 7 has no authored regions yet: --write must merge the clustering
    as colour-sample regions with region_ids always LISTS, and the written
    file must still validate as schema v2."""
    authored = _copy_annotations(7, tmp_path)
    ann = authored / "7" / "annotations.json"
    assert json.loads(ann.read_text()).get("regions", []) == []

    report = run_map(
        7, write=True, authored_root=authored, out_root=tmp_path / "out"
    )
    doc = json.loads(ann.read_text())
    assert doc["regions"], "written annotations must gain regions entries"
    region_ids = {r["region_id"] for r in doc["regions"]}
    for r in doc["regions"]:
        assert r["kind"] == "colour-region"
        assert r["name"] is None
        assert r["bonus"] is None
        assert r["source"] == "colour-sample"
        assert r["confidence"] == "low"
    for t in doc["territories"]:
        assert isinstance(t["region_ids"], list), (
            f"territory {t['territory_id']} region_ids must be a list, got "
            f"{type(t['region_ids']).__name__}"
        )
        assert len(t["region_ids"]) == 1
        assert t["region_ids"][0] in region_ids
    assert len(doc["regions"]) == report["counts"]["n_clusters"]

    # the written file still passes the schema-v2 structural validation
    summary = cat.load_catalog()[7]
    validate_annotations(doc, summary.width, summary.height)

    # re-running --write over its own colour-sample output needs no --force
    run_map(7, write=True, authored_root=authored, out_root=tmp_path / "out")


def test_map9_without_catalog_entry_samples_without_crashing(tmp_path):
    assert 9 not in cat.load_catalog(), "test premise: map 9 is uncatalogued"
    report = run_map(9, out_root=tmp_path / "out")
    assert report["counts"]["n_territories"] == 30
    assert report["counts"]["catalog_num_regions"] is None
    assert report["counts"]["clusters_match_catalog"] is None
    assert report["counts"]["n_clusters"] >= 1
    # dimensions came from the artwork itself
    with Image.open(cat.image_path(9)) as im:
        assert report["image_size"] == list(im.size)
    assert (tmp_path / "out" / "9" / "region_sample.json").is_file()
    assert (tmp_path / "out" / "9" / "region_overlay.png").is_file()
