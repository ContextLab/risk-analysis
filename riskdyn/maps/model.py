"""Core map types."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MapSummary:
    """Catalog metadata for one map, as listed on /maps."""

    map_id: int
    name: str
    width: int
    height: int
    num_territories: int
    num_regions: int
    num_games_total: int
    num_games_recent: int
    caps: int
    image_url: str
    thumbnail_url: str
    size: str
    recommended_min_players: int
    recommended_max_players: int
