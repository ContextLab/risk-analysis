import pytest

from riskdyn.sources.d12.parse_topology import parse_topology

FIXTURE_NAME = "game_map1_territories.html"


@pytest.fixture
def world_classic(fixtures_dir):
    return parse_topology((fixtures_dir / FIXTURE_NAME).read_text(), map_id=1)


def test_world_classic_has_42_territories(world_classic):
    assert len(world_classic.territories) == 42


def test_adjacency_is_symmetric(world_classic):
    for t in world_classic.territories:
        for neighbour_id in t.adjacencies:
            assert t.territory_id in world_classic.by_id[neighbour_id].adjacencies, (
                f"{t.territory_id} -> {neighbour_id} is not reciprocated"
            )


def test_world_classic_has_83_edges(world_classic):
    # Verified against the real page: 42 territories, 83 undirected borders.
    edges = sum(len(t.adjacencies) for t in world_classic.territories)
    assert edges % 2 == 0
    assert edges // 2 == 83


def test_every_adjacency_refers_to_a_known_territory(world_classic):
    known = set(world_classic.by_id)
    for t in world_classic.territories:
        assert set(t.adjacencies) <= known, f"{t.territory_id} cites unknown neighbours"


def test_every_territory_has_a_name_and_coordinates(world_classic):
    # NOTE: region_id is deliberately NOT asserted here. D12's markup carries no
    # continent membership at all (see the revision table above); it defaults to 0.
    assert all(t.name for t in world_classic.territories)
    assert all(t.x > 0 and t.y > 0 for t in world_classic.territories)


def test_known_territory_names_are_parsed(world_classic):
    names = {t.name for t in world_classic.territories}
    assert {"Northwest Territory", "Ontario", "Greenland", "Kamchatka"} <= names


def test_no_territory_is_isolated(world_classic):
    assert all(t.adjacencies for t in world_classic.territories)


def test_raises_on_markup_without_territories():
    with pytest.raises(ValueError, match="territory"):
        parse_topology("<html><body>nothing here</body></html>", map_id=1)
