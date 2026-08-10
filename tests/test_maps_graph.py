import networkx as nx
import pytest
from riskdyn.maps.graph import check_invariants, region_subgraphs, to_graph
from riskdyn.maps.model import MapTopology, Territory


def triangle() -> MapTopology:
    return MapTopology(map_id=99, territories=(
        Territory(1, "A", 1, 10, 10, (2, 3)),
        Territory(2, "B", 1, 20, 10, (1, 3)),
        Territory(3, "C", 2, 30, 10, (1, 2)),
    ))


def test_graph_has_a_node_per_territory():
    g = to_graph(triangle())
    assert set(g.nodes) == {1, 2, 3}
    assert g.nodes[1]["name"] == "A"
    assert g.nodes[3]["region_id"] == 2


def test_edges_are_undirected_and_deduplicated():
    g = to_graph(triangle())
    assert g.number_of_edges() == 3
    assert g.has_edge(1, 2) and g.has_edge(2, 1)


def test_clean_topology_reports_no_violations():
    assert check_invariants(to_graph(triangle())) == []


def test_disconnected_map_is_reported():
    topo = MapTopology(map_id=99, territories=(
        Territory(1, "A", 1, 0, 0, (2,)),
        Territory(2, "B", 1, 1, 0, (1,)),
        Territory(3, "island", 2, 9, 9, ()),
    ))
    violations = check_invariants(to_graph(topo))
    assert any("connected" in v for v in violations)


def test_self_loop_is_reported():
    topo = MapTopology(map_id=99, territories=(
        Territory(1, "A", 1, 0, 0, (1, 2)),
        Territory(2, "B", 1, 1, 0, (1,)),
    ))
    assert any("self-loop" in v for v in check_invariants(to_graph(topo)))


def test_region_subgraphs_partition_the_territories():
    subs = region_subgraphs(to_graph(triangle()))
    assert set(subs) == {1, 2}
    assert sum(s.number_of_nodes() for s in subs.values()) == 3


def test_map_with_no_regions_is_not_a_violation():
    # "Brecourt Manor" (map 77) has 34 territories and 0 regions — a variant
    # with no continent bonuses. Every territory has region_id 0, and that must
    # not produce 34 spurious violations.
    topo = MapTopology(map_id=77, territories=(
        Territory(1, "A", 0, 0, 0, (2,)),
        Territory(2, "B", 0, 1, 0, (1, 3)),
        Territory(3, "C", 0, 2, 0, (2,)),
    ))
    assert check_invariants(to_graph(topo)) == []


def test_partial_region_assignment_is_a_violation():
    # A map where *some* territories have regions and others do not indicates a
    # parse failure, unlike a map with no regions at all.
    topo = MapTopology(map_id=99, territories=(
        Territory(1, "A", 1, 0, 0, (2,)),
        Territory(2, "B", 0, 1, 0, (1, 3)),
        Territory(3, "C", 2, 2, 0, (2,)),
    ))
    assert any("without a region" in v for v in check_invariants(to_graph(topo)))
