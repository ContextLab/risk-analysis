"""Label placement: collision-free, deterministic, complete.

These tests check the *rendered* label geometry (matplotlib window extents),
not the requested offsets, and they run against the real World Classic
topology plus a synthetic 150-territory map for the dense end of the catalog.
"""
import pathlib
import subprocess
import sys
import time

import matplotlib.pyplot as plt
import matplotlib.text as mtext
import pytest

from riskdyn.maps.model import MapTopology, Territory
from riskdyn.maps.render import _build_figure, render_map
from riskdyn.sources.d12.parse_topology import parse_topology

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def world_classic() -> MapTopology:
    html = (FIXTURES / "game_map1_territories.html").read_text()
    return parse_topology(html, map_id=1)


def dense_grid(n_cols: int = 15, n_rows: int = 10) -> MapTopology:
    """Synthetic 150-territory map at the dense end of the catalog's range.

    Deterministic staggered grid (no randomness): spacing comparable to World
    Classic's crowded regions, 4-neighbour adjacency. We have no real D12
    topology this large, so dense-map behaviour is exercised synthetically.
    """
    territories = []
    for row in range(n_rows):
        for col in range(n_cols):
            tid = row * n_cols + col + 1
            adjacencies = []
            if col > 0:
                adjacencies.append(tid - 1)
            if col < n_cols - 1:
                adjacencies.append(tid + 1)
            if row > 0:
                adjacencies.append(tid - n_cols)
            if row < n_rows - 1:
                adjacencies.append(tid + n_cols)
            x = 60 * col + 8 * ((row * n_cols + col) % 5) - 16
            y = 55 * row + 6 * ((row + 2 * col) % 7) - 18
            territories.append(
                Territory(tid, f"Territory {tid:03d}", (row // 2) + 1,
                          x, y, tuple(adjacencies))
            )
    return MapTopology(map_id=999, territories=tuple(territories))


def rendered_label_extents(fig, artists):
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    # Text.get_window_extent, not Annotation.get_window_extent: the latter
    # unions in the leader-line arrow patch, and what must not collide is the
    # rendered text itself.
    return [mtext.Text.get_window_extent(a, renderer=renderer) for a in artists]


def overlap_area(a, b) -> float:
    w = min(a.x1, b.x1) - max(a.x0, b.x0)
    h = min(a.y1, b.y1) - max(a.y0, b.y0)
    return w * h if (w > 0 and h > 0) else 0.0


def assert_no_label_overlaps(fig, artists):
    extents = rendered_label_extents(fig, artists)
    offenders = [
        (artists[i].get_text(), artists[j].get_text(), overlap_area(extents[i], extents[j]))
        for i in range(len(extents))
        for j in range(i + 1, len(extents))
        if overlap_area(extents[i], extents[j]) > 0.0
    ]
    assert not offenders, f"overlapping label pairs: {offenders}"


def test_world_classic_labels_do_not_overlap():
    fig, labels = _build_figure(world_classic(), width=1021, height=689,
                                title="World Classic")
    try:
        assert_no_label_overlaps(fig, labels)
    finally:
        plt.close(fig)


def test_world_classic_every_territory_has_exactly_one_label():
    topology = world_classic()
    fig, labels = _build_figure(topology, width=1021, height=689)
    try:
        assert sorted(a.get_text() for a in labels) == sorted(
            t.name for t in topology.territories
        )
    finally:
        plt.close(fig)


def test_svg_render_is_deterministic_across_runs(tmp_path):
    topology = world_classic()
    first = render_map(topology, tmp_path / "a.svg", width=1021, height=689,
                       title="World Classic")
    second = render_map(topology, tmp_path / "b.svg", width=1021, height=689,
                        title="World Classic")
    assert first.read_bytes() == second.read_bytes()


def test_svg_render_is_deterministic_across_processes(tmp_path):
    """Byte-identical output from a fresh interpreter, not just a re-call.

    Catches per-process nondeterminism (unsalted SVG ids, embedded
    timestamps) that a same-process double render cannot see.
    """
    topology = world_classic()
    in_process = render_map(topology, tmp_path / "in.svg", width=1021, height=689)
    script = f"""
import sys
sys.path.insert(0, {str(pathlib.Path(__file__).parent)!r})
from test_render_labels import world_classic
from riskdyn.maps.render import render_map
render_map(world_classic(), {str(tmp_path / "sub.svg")!r}, width=1021, height=689)
"""
    subprocess.run([sys.executable, "-c", script], check=True)
    assert in_process.read_bytes() == (tmp_path / "sub.svg").read_bytes()


def test_dense_synthetic_map_no_overlaps_within_time_bound(tmp_path):
    topology = dense_grid()
    assert len(topology.territories) == 150
    start = time.monotonic()
    fig, labels = _build_figure(topology, width=1021, height=689)
    try:
        placement_elapsed = time.monotonic() - start
        assert placement_elapsed < 30.0, f"placement took {placement_elapsed:.1f}s"
        assert len(labels) == 150
        assert_no_label_overlaps(fig, labels)
    finally:
        plt.close(fig)
    # End-to-end save must also stay sane.
    start = time.monotonic()
    out = render_map(topology, tmp_path / "dense.svg", width=1021, height=689)
    assert time.monotonic() - start < 30.0
    assert out.stat().st_size > 1000
