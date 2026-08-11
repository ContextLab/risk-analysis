from riskdyn.sources.d12.parse_catalog import parse_catalog


def test_parses_every_map_in_the_catalog(fixtures_dir):
    maps = parse_catalog((fixtures_dir / "maps_page.html").read_text())
    # The fixture is frozen and committed, so the site-growth hedge (">= 70")
    # no longer applies: it must be exactly the 77 maps captured in the fixture.
    assert len(maps) == 77
    assert len({m.map_id for m in maps}) == len(maps)


def test_world_classic_fields_match_the_site(fixtures_dir):
    maps = parse_catalog((fixtures_dir / "maps_page.html").read_text())
    wc = next(m for m in maps if m.map_id == 1)
    assert wc.name == "World Classic"
    assert wc.num_territories == 42
    assert wc.num_regions == 6
    assert (wc.width, wc.height) == (1021, 689)
    assert wc.num_games_total > 500_000
    assert wc.image_url.endswith("/1.large.jpg")


def test_territory_counts_are_plausible(fixtures_dir):
    maps = parse_catalog((fixtures_dir / "maps_page.html").read_text())
    assert all(24 <= m.num_territories <= 200 for m in maps)
    # NOT `>= 1`: "Brecourt Manor" (map 77) genuinely has 0 regions — a variant
    # with no continent bonuses, 34 territories and 4,035 games played. Region
    # count ranges 0-36 across the catalog.
    assert all(0 <= m.num_regions <= 40 for m in maps)


def test_regionless_map_is_represented_not_rejected(fixtures_dir):
    maps = parse_catalog((fixtures_dir / "maps_page.html").read_text())
    brecourt = next(m for m in maps if m.name == "Brecourt Manor")
    assert brecourt.num_regions == 0
    assert brecourt.num_territories == 34


def test_raises_on_html_without_a_catalog():
    import pytest
    with pytest.raises(ValueError, match="catalog"):
        parse_catalog("<html><body>no catalog here</body></html>")
