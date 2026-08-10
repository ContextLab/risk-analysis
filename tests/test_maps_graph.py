import pathlib
import networkx as nx
import pytest
from riskdyn.maps.graph import check_invariants, region_subgraphs, to_graph
from riskdyn.maps.model import MapTopology, Territory
from riskdyn.sources.d12.parse_topology import parse_topology


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


def test_dangling_adjacency_reference_is_reported():
    # A territory that lists a neighbour id with no corresponding Territory
    # creates a phantom node. This parse bug should be reported loudly.
    topo = MapTopology(map_id=99, territories=(
        Territory(1, "A", 1, 0, 0, (2, 999)),  # 999 doesn't exist
        Territory(2, "B", 1, 1, 0, (1,)),
    ))
    violations = check_invariants(to_graph(topo))
    assert any("unknown territory ids" in v for v in violations)


def test_region_subgraphs_handles_phantom_nodes():
    # region_subgraphs should not raise KeyError on phantom nodes (nodes created
    # by add_edge that carry no attributes). It should place them in region 0.
    topo = MapTopology(map_id=99, territories=(
        Territory(1, "A", 1, 0, 0, (2, 999)),  # 999 doesn't exist
        Territory(2, "B", 1, 1, 0, (1,)),
    ))
    g = to_graph(topo)
    # This should not raise
    subs = region_subgraphs(g)
    # Phantom node should be in region 0
    assert 0 in subs
    assert 999 in subs[0].nodes


def test_well_formed_topology_still_clean():
    # Regression test: ensure well-formed topologies still report no violations.
    topo = MapTopology(map_id=99, territories=(
        Territory(1, "A", 1, 0, 0, (2, 3)),
        Territory(2, "B", 1, 1, 0, (1, 3)),
        Territory(3, "C", 1, 2, 0, (1, 2)),
    ))
    assert check_invariants(to_graph(topo)) == []


@pytest.fixture
def world_classic_graph(fixtures_dir):
    """Parse the real fixture and convert to graph."""
    fixture_path = pathlib.Path(fixtures_dir) / "game_map1_territories.html"
    topo = parse_topology(fixture_path.read_text(), map_id=1)
    return to_graph(topo)


def test_real_fixture_graph_structure(world_classic_graph):
    # The real fixture should have 42 nodes and 83 edges.
    assert world_classic_graph.number_of_nodes() == 42
    assert world_classic_graph.number_of_edges() == 83


def test_real_fixture_graph_is_valid(world_classic_graph):
    # The real fixture should produce zero violations when converted to a graph.
    violations = check_invariants(world_classic_graph)
    assert violations == [], f"Real fixture should be valid but got: {violations}"
