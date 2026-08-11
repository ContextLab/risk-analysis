"""Offline access to the D12 map catalog.

The pipeline needs each map's ``width``/``height`` (for the dimension check)
and ``num_territories`` (for the confidence report).  No network access is
allowed here: the catalog is read from ``data/raw/map_catalog.json`` when the
CLI has materialized it, otherwise from the checked-in ``/maps`` page snapshot
at ``tests/fixtures/maps_page.html``.
"""
from __future__ import annotations

import json
import pathlib

from riskdyn.maps.model import MapSummary
from riskdyn.sources.d12.parse_catalog import parse_catalog

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CATALOG_JSON = REPO_ROOT / "data" / "raw" / "map_catalog.json"
MAPS_PAGE_SNAPSHOT = REPO_ROOT / "tests" / "fixtures" / "maps_page.html"
MAP_IMAGES_DIR = REPO_ROOT / "data" / "raw" / "map_images"


def load_catalog(path: str | pathlib.Path | None = None) -> dict[int, MapSummary]:
    """Return {map_id: MapSummary} without touching the network.

    Args:
        path: explicit ``.json`` (CLI ``pull-catalog`` output) or ``.html``
            (a saved ``/maps`` page).  When None, tries ``CATALOG_JSON`` then
            ``MAPS_PAGE_SNAPSHOT``.
    """
    if path is None:
        path = CATALOG_JSON if CATALOG_JSON.is_file() else MAPS_PAGE_SNAPSHOT
    path = pathlib.Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"no catalog source at {path}")
    if path.suffix == ".json":
        entries = [MapSummary(**e) for e in json.loads(path.read_text())]
    else:
        entries = parse_catalog(path.read_text())
    return {m.map_id: m for m in sorted(entries, key=lambda m: m.map_id)}


def image_path(map_id: int, images_dir: str | pathlib.Path | None = None) -> pathlib.Path:
    """Path of the already-downloaded artwork for one map."""
    images_dir = pathlib.Path(images_dir) if images_dir else MAP_IMAGES_DIR
    return images_dir / f"{map_id}.large.jpg"
