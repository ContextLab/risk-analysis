import pytest
from riskdyn.maps.model import MapTopology, Territory
from riskdyn.maps.render import render_map


def square() -> MapTopology:
    return MapTopology(map_id=99, territories=(
        Territory(1, "Alpha", 1, 10, 10, (2, 3)),
        Territory(2, "Beta", 1, 90, 10, (1, 4)),
        Territory(3, "Gamma", 2, 10, 90, (1, 4)),
        Territory(4, "Delta", 2, 90, 90, (2, 3)),
    ))


@pytest.mark.parametrize("suffix", [".pdf", ".svg", ".png"])
def test_renders_each_format(tmp_path, suffix):
    out = render_map(square(), tmp_path / f"map{suffix}", width=1021, height=689)
    assert out.exists()
    assert out.stat().st_size > 1000


def test_pdf_output_is_vector_not_raster(tmp_path):
    out = render_map(square(), tmp_path / "map.pdf", width=1021, height=689)
    body = out.read_bytes()
    assert body.startswith(b"%PDF")
    # A vector PDF contains no embedded image XObject for this content.
    assert b"/Subtype /Image" not in body


def test_svg_contains_every_territory_label(tmp_path):
    out = render_map(square(), tmp_path / "map.svg", width=1021, height=689)
    svg = out.read_text()
    for name in ("Alpha", "Beta", "Gamma", "Delta"):
        assert name in svg


def test_svg_labels_are_real_text_elements(tmp_path):
    """SVG labels must be real <text> elements, not path-based glyphs."""
    out = render_map(square(), tmp_path / "map.svg", width=1021, height=689)
    svg = out.read_text()
    assert "<text" in svg


def test_svg_territory_name_appears_as_text_content(tmp_path):
    """Territory names must appear as text content inside <text>, not just comments."""
    out = render_map(square(), tmp_path / "map.svg", width=1021, height=689)
    svg = out.read_text()
    # Check that at least one territory name appears as text content
    # between opening and closing text tags, not inside a comment
    for name in ("Alpha", "Beta", "Gamma", "Delta"):
        # Find a text element containing this name
        import re
        # Match <text...>...name...</text> patterns, ensuring it's not in a comment
        pattern = rf"<text[^>]*>(?:(?!</)(?!<!--).)*{re.escape(name)}(?:(?!</)(?!<!--).)*</text>"
        assert re.search(pattern, svg), f"Territory name '{name}' not found as text content in <text> element"


def test_render_map_survives_a_phantom_node(tmp_path):
    """A phantom node (adjacency referencing an unknown territory id) makes
    to_graph add a node with none of the usual attributes — check_invariants
    explicitly detects and reports this. render_map must degrade to a
    visibly-wrong plot instead of dying on a bare KeyError.
    """
    topo = MapTopology(map_id=99, territories=(
        Territory(1, "Alpha", 1, 10, 10, (2, 999)),  # 999 has no Territory
        Territory(2, "Beta", 1, 90, 10, (1,)),
    ))
    out = render_map(topo, tmp_path / "map.pdf", width=1021, height=689)
    assert out.exists()
    assert out.stat().st_size > 1000
