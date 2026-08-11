"""Colour-sampled region proposals from D12's own label coordinates.

D12's markup carries territory positions but NO continent membership, so
region structure must come from the artwork.  Because every map now has
D12's own node coordinates (issue #4, schema v2 annotations), we can sample
the artwork colour at a known-correct point inside each territory instead
of segmenting anything.

Where the sample is taken
    D12 positions each territory marker by the CSS TOP-LEFT of a
    ``territory-large`` div (``style="left: Xpx; top: Ypx"`` matches
    ``data-x``/``data-y`` exactly -- see tests/fixtures/mappanel_map1.html,
    e.g. territory 5).  The label box is ~30x20, so the point actually
    inside the territory is the box CENTER: ``(x + 15, y + 10)``.  Sampling
    at the raw ``(x, y)`` lands on coastlines and ocean for a third of map
    1's territories (measured: mean patch consistency 0.43-0.76 across
    maps at (0,0) vs 0.92-1.00 at (+15,+10)).

How the colour is read
    A small patch (default 9x9) around the sample point, converted to CIE
    Lab, reduced by the per-channel MEDIAN -- never the mean -- so a stray
    border stroke, label glyph or coastline pixel cannot drag the value.
    The fraction of patch pixels within ``CONSISTENCY_LAB_TOL`` of that
    median is reported per territory: a low fraction means the patch
    straddles a boundary or text and the sample is UNRELIABLE.  Unreliable
    samples are flagged and ringed in the overlay, never silently used as
    truth.

How colours become clusters
    Single-linkage agglomerative clustering in Lab.  The distance
    threshold is derived from the data by default: every midpoint between
    consecutive sorted single-linkage merge distances (= MST edge weights)
    is a candidate cut, and the cut maximizing the mean silhouette score
    is chosen.  A plain largest-gap rule fails on map 1 -- the five
    between-continent merges (18..45) spread wider than the boundary gap
    at ~16.5 -- while the silhouette cut recovers the six continents
    exactly.  The cluster count is NEVER forced to the catalog's
    ``num_regions``: on map 7 the catalog says 12 regions but only 6 are
    colour groups (the rest are overlapping legend-only city bonuses), and
    grey territories belong to no region at all.

Everything this module writes is PROPOSAL-QUALITY (source
``colour-sample``, confidence ``low``).  ``--write`` refuses to touch an
annotations file whose regions came from a human or a vision legend read
unless ``--force`` is given.

CLI:
    ./.venv/bin/python -m riskdyn.workbench.regions <map_id> [--write]
        [--force] [--patch N] [--threshold T]
    ./.venv/bin/python -m riskdyn.workbench.regions --all
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import sys

import numpy as np

from riskdyn.segment import catalog as cat
from riskdyn.workbench.build import AUTHORED_ROOT, PROCESSED_ROOT
from riskdyn.workbench.graph_build import _summary_or_fallback, load_annotations_v2
from riskdyn.workbench.provenance import make_provenance

# Center offset of D12's ~30x20 "territory-large" label box whose top-left
# is data-x/data-y (tests/fixtures/mappanel_map1.html).
LABEL_BOX_OFFSET = (15, 10)
DEFAULT_PATCH = 9
# A patch pixel within this Lab distance of the patch median counts as
# agreeing with it; the agreeing fraction is the consistency measure.
CONSISTENCY_LAB_TOL = 8.0
# Below this consistency fraction a sample is flagged unreliable (a node on
# a border stroke or two-colour boundary measures ~0.0-0.5).
RELIABLE_MIN_FRACTION = 0.6

WRITE_SOURCE = "colour-sample"


# --------------------------------------------------------------------------
# colour space
# --------------------------------------------------------------------------

_SRGB_TO_XYZ = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ]
)
_XYZ_TO_SRGB = np.linalg.inv(_SRGB_TO_XYZ)
_D65 = np.array([0.95047, 1.0, 1.08883])
_DELTA = 6 / 29


def srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB in [0, 1], shape (..., 3) -> CIE Lab (D65)."""
    rgb = np.asarray(rgb, dtype=float)
    lin = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    t = (lin @ _SRGB_TO_XYZ.T) / _D65
    f = np.where(t > _DELTA**3, np.cbrt(t), t / (3 * _DELTA**2) + 4 / 29)
    L = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


def lab_to_srgb(lab: np.ndarray) -> np.ndarray:
    """CIE Lab -> sRGB in [0, 1] (gamut-clipped)."""
    lab = np.asarray(lab, dtype=float)
    fy = (lab[..., 0] + 16) / 116
    fx = fy + lab[..., 1] / 500
    fz = fy - lab[..., 2] / 200
    f = np.stack([fx, fy, fz], axis=-1)
    t = np.where(f > _DELTA, f**3, 3 * _DELTA**2 * (f - 4 / 29))
    lin = np.clip((t * _D65) @ _XYZ_TO_SRGB.T, 0.0, 1.0)
    return np.where(lin <= 0.0031308, 12.92 * lin, 1.055 * lin ** (1 / 2.4) - 0.055)


def lab_to_hex(lab) -> str:
    rgb = np.round(lab_to_srgb(np.asarray(lab)) * 255).astype(int)
    return "#{:02x}{:02x}{:02x}".format(*rgb.tolist())


# --------------------------------------------------------------------------
# sampling
# --------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class TerritorySample:
    territory_id: int
    name: str
    x: int  # D12 label-box top-left, as authored
    y: int
    sample_x: int  # label-box center actually sampled
    sample_y: int
    lab: tuple[float, float, float]
    hex: str
    patch_consistency: float
    reliable: bool


def sample_label_point(
    image: np.ndarray, x: int, y: int, patch: int = DEFAULT_PATCH
) -> tuple[np.ndarray, float, tuple[int, int]]:
    """Median Lab + consistency fraction of the patch at the label center.

    ``(x, y)`` is D12's label-box top-left as authored; the patch is
    centered on the box center ``(x, y) + LABEL_BOX_OFFSET`` and clipped to
    the image.  Returns ``(median_lab, consistency_fraction, (sx, sy))``.
    """
    if patch < 1 or patch % 2 == 0:
        raise ValueError(f"patch must be a positive odd integer, got {patch}")
    h, w = image.shape[:2]
    sx = min(max(x + LABEL_BOX_OFFSET[0], 0), w - 1)
    sy = min(max(y + LABEL_BOX_OFFSET[1], 0), h - 1)
    r = patch // 2
    pixels = image[
        max(0, sy - r): sy + r + 1, max(0, sx - r): sx + r + 1
    ].reshape(-1, 3)
    lab = srgb_to_lab(pixels / 255.0)
    median = np.median(lab, axis=0)
    fraction = float(
        np.mean(np.linalg.norm(lab - median, axis=1) <= CONSISTENCY_LAB_TOL)
    )
    return median, fraction, (sx, sy)


def sample_territories(
    image: np.ndarray, territories: list[dict], patch: int = DEFAULT_PATCH
) -> list[TerritorySample]:
    samples = []
    for t in sorted(territories, key=lambda t: t["territory_id"]):
        median, fraction, (sx, sy) = sample_label_point(
            image, t["x"], t["y"], patch
        )
        samples.append(
            TerritorySample(
                territory_id=t["territory_id"],
                name=t["name"],
                x=t["x"],
                y=t["y"],
                sample_x=sx,
                sample_y=sy,
                lab=tuple(round(float(v), 2) for v in median),
                hex=lab_to_hex(median),
                patch_consistency=round(fraction, 3),
                reliable=fraction >= RELIABLE_MIN_FRACTION,
            )
        )
    return samples


# --------------------------------------------------------------------------
# single-linkage clustering with a data-derived threshold
# --------------------------------------------------------------------------

def _mst_edges(labs: np.ndarray) -> list[tuple[int, int, float]]:
    """Minimum spanning tree (Prim) over pairwise Lab distances.

    Single-linkage components below any threshold t are exactly the
    components after cutting MST edges with weight >= t.
    """
    n = len(labs)
    if n < 2:
        return []
    dist = np.linalg.norm(labs[:, None] - labs[None, :], axis=-1)
    in_tree = np.zeros(n, dtype=bool)
    in_tree[0] = True
    best = dist[0].copy()
    best_from = np.zeros(n, dtype=int)
    edges: list[tuple[int, int, float]] = []
    for _ in range(n - 1):
        j = int(np.argmin(np.where(in_tree, np.inf, best)))
        edges.append((int(best_from[j]), j, float(best[j])))
        in_tree[j] = True
        closer = dist[j] < best
        best_from[closer] = j
        best = np.minimum(best, dist[j])
    return edges


def _components(n: int, edges: list[tuple[int, int, float]], threshold: float) -> np.ndarray:
    """0-based component label per index, cutting MST edges >= threshold."""
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b, w in edges:
        if w < threshold:
            parent[find(a)] = find(b)
    labels = np.empty(n, dtype=int)
    roots: dict[int, int] = {}
    for i in range(n):
        labels[i] = roots.setdefault(find(i), len(roots))
    return labels


def _mean_silhouette(dist: np.ndarray, labels: np.ndarray) -> float:
    """Mean silhouette; singleton clusters score 0 for their lone member."""
    clusters = sorted(set(labels.tolist()))
    if len(clusters) < 2:
        return -1.0
    scores = np.zeros(len(labels))
    for i in range(len(labels)):
        same = labels == labels[i]
        same_others = same.copy()
        same_others[i] = False
        if not same_others.any():
            continue  # singleton: silhouette 0 by convention
        a = dist[i][same_others].mean()
        b = min(dist[i][labels == k].mean() for k in clusters if k != labels[i])
        scores[i] = (b - a) / max(a, b) if max(a, b) > 0 else 0.0
    return float(scores.mean())


THRESHOLD_METHOD_DERIVED = (
    "max-mean-silhouette cut: candidate thresholds are midpoints between "
    "consecutive sorted single-linkage merge distances (MST edge weights); "
    "the candidate maximizing the mean silhouette score in Lab is chosen. "
    "(A plain largest-gap rule mis-cuts map 1: the between-continent merges "
    "spread wider than the within/between boundary gap.)"
)
THRESHOLD_METHOD_USER = "user-specified via --threshold"


def choose_threshold(
    labs: np.ndarray, edges: list[tuple[int, int, float]]
) -> tuple[float, str]:
    """Data-derived single-linkage cut (see THRESHOLD_METHOD_DERIVED)."""
    weights = sorted(w for _, _, w in edges)
    if not weights:
        return 0.0, "single territory: no clustering needed"
    dist = np.linalg.norm(labs[:, None] - labs[None, :], axis=-1)
    best_score, best_threshold = -np.inf, weights[-1] + 1.0
    for lo, hi in zip(weights, weights[1:]):
        if hi - lo <= 1e-9:
            continue
        candidate = (lo + hi) / 2
        score = _mean_silhouette(dist, _components(len(labs), edges, candidate))
        if score > best_score:
            best_score, best_threshold = score, candidate
    return float(best_threshold), THRESHOLD_METHOD_DERIVED


@dataclasses.dataclass(frozen=True)
class Cluster:
    cluster_id: int
    size: int
    mean_lab: tuple[float, float, float]
    mean_hex: str
    territory_ids: tuple[int, ...]
    territory_names: tuple[str, ...]


def cluster_samples(
    samples: list[TerritorySample], threshold: float | None = None
) -> tuple[list[Cluster], dict[int, int], float, str]:
    """Cluster sampled Lab colours; returns (clusters, tid->cluster_id,
    threshold_used, threshold_method).  Cluster ids are 1..k by descending
    size (ties: smallest member territory_id first)."""
    labs = np.array([s.lab for s in samples], dtype=float)
    edges = _mst_edges(labs)
    if threshold is None:
        threshold, method = choose_threshold(labs, edges)
    else:
        threshold, method = float(threshold), THRESHOLD_METHOD_USER
    raw = _components(len(samples), edges, threshold)
    groups: dict[int, list[int]] = {}
    for idx, lab in enumerate(raw.tolist()):
        groups.setdefault(lab, []).append(idx)
    ordered = sorted(
        groups.values(),
        key=lambda idxs: (-len(idxs), min(samples[i].territory_id for i in idxs)),
    )
    clusters: list[Cluster] = []
    assignment: dict[int, int] = {}
    for cid, idxs in enumerate(ordered, start=1):
        mean_lab = labs[idxs].mean(axis=0)
        clusters.append(
            Cluster(
                cluster_id=cid,
                size=len(idxs),
                mean_lab=tuple(round(float(v), 2) for v in mean_lab),
                mean_hex=lab_to_hex(mean_lab),
                territory_ids=tuple(sorted(samples[i].territory_id for i in idxs)),
                territory_names=tuple(
                    s.name
                    for s in sorted(
                        (samples[i] for i in idxs), key=lambda s: s.territory_id
                    )
                ),
            )
        )
        for i in idxs:
            assignment[samples[i].territory_id] = cid
    return clusters, assignment, threshold, method


# --------------------------------------------------------------------------
# report + overlay
# --------------------------------------------------------------------------

def build_report(
    map_id: int,
    summary,
    samples: list[TerritorySample],
    clusters: list[Cluster],
    assignment: dict[int, int],
    threshold: float,
    threshold_method: str,
    patch: int,
    ann_rel: str,
) -> dict:
    unreliable = [s.territory_id for s in samples if not s.reliable]
    expected = summary.num_regions
    return {
        "schema_version": 1,
        "map_id": map_id,
        "map_name": summary.name,
        "image_size": [summary.width, summary.height],
        "sampling": {
            "patch": patch,
            "label_box_offset": list(LABEL_BOX_OFFSET),
            "offset_rationale": (
                "D12 positions the ~30x20 territory-large label div by its "
                "CSS top-left = data-x/data-y; the point inside the "
                "territory is the box center"
            ),
            "statistic": "per-channel median in CIE Lab",
            "consistency_lab_tolerance": CONSISTENCY_LAB_TOL,
            "reliable_min_fraction": RELIABLE_MIN_FRACTION,
        },
        "threshold": {"value": round(threshold, 4), "method": threshold_method},
        "territories": [
            {
                "territory_id": s.territory_id,
                "name": s.name,
                "x": s.x,
                "y": s.y,
                "sample_x": s.sample_x,
                "sample_y": s.sample_y,
                "lab": list(s.lab),
                "hex": s.hex,
                "cluster_id": assignment[s.territory_id],
                "patch_consistency": s.patch_consistency,
                "reliable": s.reliable,
            }
            for s in samples
        ],
        "clusters": [
            {
                "cluster_id": c.cluster_id,
                "size": c.size,
                "mean_lab": list(c.mean_lab),
                "mean_hex": c.mean_hex,
                "territory_names": list(c.territory_names),
            }
            for c in clusters
        ],
        "counts": {
            "n_territories": len(samples),
            "n_unreliable": len(unreliable),
            "unreliable_territory_ids": unreliable,
            "n_clusters": len(clusters),
            "catalog_num_regions": expected,
            "clusters_match_catalog": (
                None if expected is None else len(clusters) == expected
            ),
            "note": (
                "a cluster-count mismatch is NOT automatically an error: "
                "legend-only bonuses (e.g. map 7 city bonuses) are not "
                "colour groups, and grey territories belong to no region"
            ),
        },
        "provenance": make_provenance(
            "riskdyn.workbench.regions",
            "median Lab patch sample at D12 label-box centers, "
            "single-linkage clustering, silhouette-chosen cut",
            inputs=[ann_rel, f"data/raw/map_images/{map_id}.large.jpg"],
            note="proposal-quality colour clustering; not human-verified",
        ),
    }


def write_overlay(
    image: np.ndarray,
    samples: list[TerritorySample],
    clusters: list[Cluster],
    assignment: dict[int, int],
    header: str,
    path: pathlib.Path,
) -> None:
    """Artwork with each node drawn in its cluster's mean colour, labelled
    with the cluster id; unreliable samples ringed in red.  This is how a
    human checks the clustering at a glance."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    h, w = image.shape[:2]
    fig, ax = plt.subplots(figsize=(w / 72, h / 72), dpi=144)
    ax.imshow(image)
    colour = {
        c.cluster_id: lab_to_srgb(np.array(c.mean_lab)).tolist() for c in clusters
    }
    for s in samples:
        cid = assignment[s.territory_id]
        if not s.reliable:
            ax.plot(
                [s.sample_x], [s.sample_y], "o", markersize=13,
                markerfacecolor="none", markeredgecolor="red",
                markeredgewidth=1.8, zorder=3,
            )
        ax.plot(
            [s.sample_x], [s.sample_y], "o", color=colour[cid],
            markeredgecolor="black", markersize=8, zorder=4,
        )
        ax.text(
            s.sample_x, s.sample_y - 8, str(cid), color="white", fontsize=6,
            ha="center", va="bottom", zorder=5,
            bbox=dict(facecolor="black", alpha=0.6, pad=0.5, lw=0),
        )
    ax.set_title(header, fontsize=8)
    ax.set_xlim(0, w)
    ax.set_ylim(h, 0)
    ax.axis("off")
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


# --------------------------------------------------------------------------
# --write: merge proposals into annotations.json
# --------------------------------------------------------------------------

class WriteRefused(RuntimeError):
    """--write would overwrite region data not sourced from colour-sample."""


def merge_into_annotations(
    ann_path: pathlib.Path,
    samples: list[TerritorySample],
    clusters: list[Cluster],
    assignment: dict[int, int],
    threshold: float,
    threshold_method: str,
    force: bool = False,
) -> None:
    doc = json.loads(ann_path.read_text())
    existing = doc.get("regions", [])
    foreign = [r for r in existing if r.get("source") != WRITE_SOURCE]
    if foreign and not force:
        names = ", ".join(
            f"{r['region_id']}:{r.get('name')!r} (source {r.get('source')!r})"
            for r in foreign
        )
        raise WriteRefused(
            f"{ann_path} already has {len(foreign)} region(s) not sourced "
            f"from {WRITE_SOURCE!r}: {names}. These came from a human or a "
            "vision legend read and colour-sample output is proposal-quality "
            "-- it must never overwrite them. Re-run with --force only if "
            "you explicitly intend to REPLACE them."
        )
    n_unreliable = sum(1 for s in samples if not s.reliable)
    doc["regions"] = [
        {
            "region_id": c.cluster_id,
            "kind": "colour-region",
            "name": None,
            "bonus": None,
            "mean_hex": c.mean_hex,
            "source": WRITE_SOURCE,
            "confidence": "low",
            "note": (
                f"proposed by riskdyn.workbench.regions (threshold "
                f"{threshold:.2f}, {n_unreliable} unreliable sample(s) on "
                "this map); NOT confirmed by a human -- grey/no-region "
                "territories and legend-only bonuses need human review"
            ),
        }
        for c in clusters
    ]
    by_tid = {t["territory_id"]: t for t in doc["territories"]}
    for s in samples:
        by_tid[s.territory_id]["region_ids"] = [assignment[s.territory_id]]
    ann_path.write_text(json.dumps(doc, indent=1))


# --------------------------------------------------------------------------
# per-map run + batch
# --------------------------------------------------------------------------

def run_map(
    map_id: int,
    patch: int = DEFAULT_PATCH,
    threshold: float | None = None,
    write: bool = False,
    force: bool = False,
    out_root: pathlib.Path | None = None,
    authored_root: pathlib.Path | None = None,
) -> dict:
    """Sample, cluster, and report one map; returns the report dict."""
    ann_path = (authored_root or AUTHORED_ROOT) / str(map_id) / "annotations.json"
    out_dir = (out_root or PROCESSED_ROOT) / str(map_id)
    summary = _summary_or_fallback(map_id)
    doc = load_annotations_v2(ann_path, map_id)

    from PIL import Image

    with Image.open(cat.image_path(map_id)) as im:
        image = np.asarray(im.convert("RGB"))

    samples = sample_territories(image, doc["territories"], patch)
    clusters, assignment, threshold_used, method = cluster_samples(
        samples, threshold
    )
    try:
        ann_rel = str(ann_path.relative_to(cat.REPO_ROOT))
    except ValueError:
        ann_rel = str(ann_path)
    report = build_report(
        map_id, summary, samples, clusters, assignment,
        threshold_used, method, patch, ann_rel,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "region_sample.json").write_text(json.dumps(report, indent=1))
    counts = report["counts"]
    header = (
        f"map {map_id} {summary.name}: {counts['n_clusters']} colour clusters "
        f"(catalog num_regions={counts['catalog_num_regions']}), "
        f"{counts['n_unreliable']} unreliable sample(s) ringed red; "
        f"threshold {threshold_used:.2f} -- PROPOSAL, not verified"
    )
    write_overlay(
        image, samples, clusters, assignment, header,
        out_dir / "region_overlay.png",
    )
    if write:
        merge_into_annotations(
            ann_path, samples, clusters, assignment,
            threshold_used, method, force,
        )
    return report


def run_all(
    patch: int = DEFAULT_PATCH,
    threshold: float | None = None,
    authored_root: pathlib.Path | None = None,
    out_root: pathlib.Path | None = None,
) -> list[dict]:
    """Run every authored map; print the triage table; return report rows."""
    root = authored_root or AUTHORED_ROOT
    map_ids = sorted(
        int(p.name)
        for p in root.iterdir()
        if p.name.isdigit() and (p / "annotations.json").is_file()
    )
    rows: list[dict] = []
    failures = 0
    print(f"{'map':>4}  {'name':<32} {'terr':>4} {'clusters':>8} "
          f"{'catalog':>7} {'unreliable':>10}")
    for map_id in map_ids:
        try:
            report = run_map(
                map_id, patch=patch, threshold=threshold,
                authored_root=authored_root, out_root=out_root,
            )
        except Exception as exc:  # loud in the table AND the exit code
            failures += 1
            print(f"{map_id:>4}  ERROR: {exc}")
            continue
        c = report["counts"]
        rows.append(report)
        cat_regions = c["catalog_num_regions"]
        print(
            f"{map_id:>4}  {report['map_name']:<32.32} "
            f"{c['n_territories']:>4} {c['n_clusters']:>8} "
            f"{cat_regions if cat_regions is not None else '-':>7} "
            f"{c['n_unreliable']:>10}"
        )
    if failures:
        raise SystemExit(f"{failures} map(s) failed; see ERROR rows above")
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="propose region membership by sampling artwork colour "
        "at D12 label positions (proposal-quality output)"
    )
    ap.add_argument("map_id", nargs="?", type=int)
    ap.add_argument("--all", action="store_true",
                    help="run every authored map and print the triage table")
    ap.add_argument("--write", action="store_true",
                    help="merge clusters into annotations.json as "
                    "colour-sample regions")
    ap.add_argument("--force", action="store_true",
                    help="allow --write to REPLACE regions authored by a "
                    "human or vision legend read")
    ap.add_argument("--patch", type=int, default=DEFAULT_PATCH,
                    help="odd patch size in pixels (default 9)")
    ap.add_argument("--threshold", type=float, default=None,
                    help="Lab distance threshold (default: derived from the "
                    "data, see report)")
    args = ap.parse_args(argv)
    if args.all == (args.map_id is not None):
        ap.error("give exactly one of <map_id> or --all")
    if args.all and (args.write or args.force):
        ap.error("--write/--force apply to a single map, not --all")

    if args.all:
        run_all(patch=args.patch, threshold=args.threshold)
        return 0

    try:
        report = run_map(
            args.map_id, patch=args.patch, threshold=args.threshold,
            write=args.write, force=args.force,
        )
    except WriteRefused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    c = report["counts"]
    print(
        f"map {args.map_id} {report['map_name']}: {c['n_territories']} "
        f"territories -> {c['n_clusters']} colour clusters "
        f"(catalog num_regions={c['catalog_num_regions']}), "
        f"{c['n_unreliable']} unreliable; threshold "
        f"{report['threshold']['value']}"
    )
    for cl in report["clusters"]:
        print(f"  cluster {cl['cluster_id']} ({cl['size']}, {cl['mean_hex']}): "
              + ", ".join(cl["territory_names"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
