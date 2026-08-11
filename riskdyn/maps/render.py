"""Vector rendering of a map's territories and connections.

D12 distributes maps as raster JPEGs, so this module produces our own vector
representation from the site's territory label coordinates plus the adjacency
graph. Output is resolution-independent (PDF/SVG), which is what the paper's
figure pipeline needs.
"""
from __future__ import annotations

import pathlib

import matplotlib
matplotlib.use("Agg")
# Emit SVG labels as real <text> elements, not path-based glyphs, for editability in Illustrator.
matplotlib.rcParams["svg.fonttype"] = "none"
import matplotlib.pyplot as plt  # noqa: E402

from riskdyn.maps.graph import to_graph  # noqa: E402
from riskdyn.maps.model import MapTopology  # noqa: E402

# Region counts run 0-36 across the D12 catalog, so a single 20-colour
# qualitative map is not enough. tab20 + tab20b + tab20c gives 60 distinct
# categorical colours. Do NOT use `.resampled(n)` here: that samples *across*
# a colormap rather than taking its discrete entries, and for small n returns
# near-identical shades (resampled(2) on tab20 yields two similar blues).
REGION_PALETTE = (
    tuple(matplotlib.colormaps["tab20"].colors)
    + tuple(matplotlib.colormaps["tab20b"].colors)
    + tuple(matplotlib.colormaps["tab20c"].colors)
)


def render_map(
    topology: MapTopology,
    out_path: str | pathlib.Path,
    *,
    width: int | None = None,
    height: int | None = None,
    title: str | None = None,
    colour_by_region: bool = True,
) -> pathlib.Path:
    """Render a map to a vector or raster image file.

    Args:
        topology: MapTopology with territory coordinates and adjacency.
        out_path: Output file path (.pdf, .svg, or .png).
        width: Aspect ratio width (not output pixel width); combined with height
               to set figure aspect ratio. Output dimensions are determined by
               content via bbox_inches="tight".
        height: Aspect ratio height (not output pixel height); combined with width
                to set figure aspect ratio.
        title: Optional title for the plot.
        colour_by_region: If True, color nodes by region_id; if False, all white.

    Returns:
        pathlib.Path to the saved file.
    """
    out_path = pathlib.Path(out_path)
    graph = to_graph(topology)

    # D12 uses screen coordinates (y grows downward); invert for plotting.
    # .get(..., 0) rather than [...]: a phantom node (an adjacency
    # referencing an unknown territory id — see check_invariants) carries
    # none of these attributes, and rendering must degrade to a visibly
    # wrong plot rather than crash with a bare KeyError.
    positions = {
        node: (data.get("x", 0), -data.get("y", 0)) for node, data in graph.nodes(data=True)
    }

    region_ids = sorted({data.get("region_id", 0) for _, data in graph.nodes(data=True)})
    colour_for = {rid: REGION_PALETTE[i % len(REGION_PALETTE)]
                  for i, rid in enumerate(region_ids)}

    aspect = (width / height) if (width and height) else 1.5
    fig, ax = plt.subplots(figsize=(12, 12 / aspect))

    for source, target in graph.edges:
        x0, y0 = positions[source]
        x1, y1 = positions[target]
        ax.plot([x0, x1], [y0, y1], color="0.55", linewidth=0.8, zorder=1)

    for node, data in graph.nodes(data=True):
        x, y = positions[node]
        colour = colour_for[data.get("region_id", 0)] if colour_by_region else "white"
        ax.scatter([x], [y], s=260, color=colour, edgecolors="black",
                   linewidths=0.8, zorder=2)
        ax.annotate(
            data.get("name") or str(node), (x, y),
            textcoords="offset points", xytext=(0, 12),
            ha="center", fontsize=7, zorder=3,
        )

    if title:
        ax.set_title(title)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(out_path, transparent=True, bbox_inches="tight")
    plt.close(fig)
    return out_path
