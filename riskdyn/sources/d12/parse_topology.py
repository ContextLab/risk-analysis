"""Extract territory topology from D12 page markup.

Territory anchors carry everything the markup exposes as data attributes:
``data-territory`` (the id), ``data-name``, ``data-x``, ``data-y``, and
``data-adjacencies`` (a comma-separated list of territory ids).

Continent membership is NOT present anywhere in D12's markup, so ``region_id``
defaults to 0 and must be supplied from another source — see issue #4.
"""
from __future__ import annotations

import html as html_module
import re

from riskdyn.maps.model import MapTopology, Territory

_ELEMENT = re.compile(r"<a\b[^>]*\bdata-adjacencies\s*=\s*\"[^\"]*\"[^>]*>")
_ATTR = re.compile(r"\bdata-([a-z\-]+)\s*=\s*\"([^\"]*)\"")


def parse_topology(html: str, map_id: int) -> MapTopology:
    territories: list[Territory] = []
    for element in _ELEMENT.findall(html):
        attrs = dict(_ATTR.findall(element))
        if "territory" not in attrs:
            continue
        raw_adj = attrs.get("adjacencies", "").strip()
        adjacencies = tuple(
            int(part) for part in raw_adj.split(",") if part.strip()
        )
        territories.append(
            Territory(
                territory_id=int(attrs["territory"]),
                name=html_module.unescape(attrs.get("name", "")),
                # D12 exposes no continent membership; 0 means "unknown region".
                region_id=int(attrs.get("region", 0)),
                x=int(float(attrs.get("x", 0))),
                y=int(float(attrs.get("y", 0))),
                adjacencies=adjacencies,
            )
        )
    if not territories:
        raise ValueError("no territory elements found (missing data-adjacencies)")
    return MapTopology(map_id=map_id, territories=tuple(territories))
