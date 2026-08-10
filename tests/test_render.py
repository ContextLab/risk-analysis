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
