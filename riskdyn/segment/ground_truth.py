"""World Classic ground truth from ``tests/fixtures/game_map1_territories.html``.

The fixture holds D12's own markup for map 1: 42 territory anchors with exact
``data-x``/``data-y`` label coordinates.  Image dimensions equal catalog
dimensions for every map, so these coordinates index directly into image
pixel space -- no registration step.
"""
from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass

ANCHOR_RE = re.compile(
    r'data-territory="(?P<tid>\d+)"[^>]*?'
    r'data-x="(?P<x>\d+)"\s+data-y="(?P<y>\d+)"\s+data-name="(?P<name>[^"]*)"'
)


@dataclass(frozen=True)
class LabelPoint:
    territory_id: int
    name: str
    x: int
    y: int


def load_label_points(fixture_path: str | pathlib.Path) -> list[LabelPoint]:
    """Parse the 42 label points, ordered by territory id."""
    html = pathlib.Path(fixture_path).read_text()
    points = [
        LabelPoint(int(m["tid"]), m["name"], int(m["x"]), int(m["y"]))
        for m in ANCHOR_RE.finditer(html)
    ]
    if not points:
        raise ValueError(f"no territory anchors found in {fixture_path}")
    return sorted(points, key=lambda p: p.territory_id)
