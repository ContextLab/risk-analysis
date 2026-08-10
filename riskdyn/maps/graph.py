"""Territory adjacency as a networkx graph.

This is the structural representation the position-strength metrics and the
map-level graph metrics are built on, so its invariants are checked explicitly
rather than assumed.
"""
from __future__ import annotations

import networkx as nx

from riskdyn.maps.model import MapTopology


def to_graph(topology: MapTopology) -> nx.Graph:
    graph = nx.Graph()
    for territory in topology.territories:
        graph.add_node(
            territory.territory_id,
            name=territory.name,
            region_id=territory.region_id,
            x=territory.x,
            y=territory.y,
        )
    for territory in topology.territories:
        for neighbour_id in territory.adjacencies:
            graph.add_edge(territory.territory_id, neighbour_id)
    return graph


def check_invariants(graph: nx.Graph) -> list[str]:
    """Return a list of invariant violations; empty means the map is well formed."""
    violations: list[str] = []

    self_loops = list(nx.selfloop_edges(graph))
    if self_loops:
        violations.append(f"self-loop edges present: {self_loops}")

    if graph.number_of_nodes() and not nx.is_connected(graph):
        components = sorted(
            (sorted(c) for c in nx.connected_components(graph)), key=len, reverse=True
        )
        violations.append(
            f"graph is not connected: {len(components)} components, "
            f"smallest {components[-1]}"
        )

    isolated = [n for n, d in graph.degree if d == 0]
    if isolated:
        violations.append(f"isolated territories: {isolated}")

    # Some maps legitimately have no regions at all — "Brecourt Manor" is a
    # 34-territory variant with no continent bonuses. Only a *partial* region
    # assignment indicates a parse failure, so check for that rather than for
    # the absence of regions.
    region_ids = [data.get("region_id", 0) for _, data in graph.nodes(data=True)]
    if any(region_ids) and not all(region_ids):
        missing = [
            n for n, data in graph.nodes(data=True) if not data.get("region_id")
        ]
        violations.append(f"territories without a region: {missing}")

    return violations


def region_subgraphs(graph: nx.Graph) -> dict[int, nx.Graph]:
    """One induced subgraph per region (continent)."""
    regions: dict[int, list[int]] = {}
    for node, data in graph.nodes(data=True):
        regions.setdefault(data["region_id"], []).append(node)
    return {rid: graph.subgraph(nodes).copy() for rid, nodes in regions.items()}
