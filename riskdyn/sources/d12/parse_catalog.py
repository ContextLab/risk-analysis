"""Parse the map catalog embedded in the /maps page.

The page ships the whole catalog as a JSON literal inside a
``new CreateGame({...}, true)`` call, so there is no HTML scraping involved and
no per-map request needed.
"""
from __future__ import annotations

import json

from riskdyn.maps.model import MapSummary

_MARKER = "new CreateGame("


def parse_catalog(html: str) -> list[MapSummary]:
    start = html.find(_MARKER)
    if start == -1:
        raise ValueError("no map catalog found in page (missing CreateGame call)")
    payload_start = start + len(_MARKER)
    catalog, _ = json.JSONDecoder().raw_decode(html[payload_start:])
    return [
        MapSummary(
            map_id=int(entry["map_id"]),
            name=entry["name"],
            width=int(entry["width"]),
            height=int(entry["height"]),
            num_territories=int(entry["num_territories"]),
            num_regions=int(entry["num_regions"]),
            num_games_total=int(entry.get("num_games_total", 0)),
            num_games_recent=int(entry.get("num_games_recent", 0)),
            caps=int(entry.get("caps", 0)),
            image_url=entry["imageUrl"],
            thumbnail_url=entry["imageThumbnailUrl"],
            size=entry.get("size", ""),
            recommended_min_players=int(entry.get("recommended_min_players", 0)),
            recommended_max_players=int(entry.get("recommended_max_players", 0)),
        )
        for entry in catalog.values()
    ]
