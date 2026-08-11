"""Score an authored map graph against a reference topology.

This is the measuring instrument for plan v4's fan-out: before authoring 76
maps by eye, we need to know what a vision pass actually recovers.  Map 1 is
the only map with independent ground truth (D12's own ``data-adjacencies``
markup), so it is the calibration case.

The central design constraint: **name mismatches must not be scored as graph
errors.**  Artwork prints abbreviated names ("NW Territory", "W. Europe")
while the markup carries canonical ones ("Northwest Territory", "Western
Europe").  Scoring those as missing territories would slander a graph that is
in fact correct.  So scoring runs in two stages -- align names first, then
score edges only in the aligned id space, and report edges touching an
unaligned territory as **unscorable** rather than silently counting them as
errors or silently dropping them.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field

# Characters NFKD does not decompose to an ASCII base letter.  Without these,
# stripping non-ASCII would delete the letter entirely: "Ålesund" and "Ølesund"
# would both fold to "lesund".  18 of the catalog's 78 maps carry accented
# territory names, so this is the common case, not an edge case.
NONDECOMPOSING = {
    "ø": "o",
    "æ": "ae",
    "œ": "oe",
    "ß": "ss",
    "đ": "d",
    "ð": "d",
    "ł": "l",
    "þ": "th",
    "ı": "i",
    "ŋ": "n",
    "ħ": "h",
}

# Token-level expansions applied after punctuation stripping.  These make the
# artwork's abbreviations and the markup's canonical names converge on one
# normal form, so most territories match exactly and fuzzy matching is only
# needed for genuine oddities.
TOKEN_EXPANSIONS = {
    "n": "north",
    "no": "north",
    "s": "south",
    "e": "east",
    "w": "west",
    "ne": "northeast",
    "nw": "northwest",
    "se": "southeast",
    "sw": "southwest",
    "northern": "north",
    "southern": "south",
    "eastern": "east",
    "western": "west",
    "us": "united states",
    "usa": "united states",
    "uk": "united kingdom",
    "terr": "territory",
    "terrs": "territories",
    "is": "island",
    "isl": "island",
    "isls": "islands",
    "mt": "mount",
    "mts": "mountains",
    "st": "saint",
    "rep": "republic",
    "gr": "great",
}

_PUNCT_RE = re.compile(r"[^a-z0-9]+")


def normalize_name(name: str) -> str:
    """Fold a printed or canonical territory name to a comparable form.

    Lowercases, folds accents to their ASCII base letter, drops punctuation,
    then expands directional and common abbreviations token-wise so "W. Europe"
    and "Western Europe" both become "west europe".

    Accent folding must preserve the base letter: "Åland" and "Öland" are two
    distinct Baltic islands on map 56, and stripping non-ASCII outright would
    fold both to "land" and merge them.
    """
    lowered = "".join(NONDECOMPOSING.get(c, c) for c in name.lower())
    decomposed = unicodedata.normalize("NFKD", lowered)
    folded = "".join(c for c in decomposed if not unicodedata.combining(c))
    tokens = [t for t in _PUNCT_RE.split(folded) if t]
    out: list[str] = []
    for token in tokens:
        out.extend(TOKEN_EXPANSIONS.get(token, token).split())
    return " ".join(out)


def name_similarity(a: str, b: str) -> float:
    """Similarity of two already-normalized names, in [0, 1]."""
    return difflib.SequenceMatcher(None, a, b).ratio()


@dataclass(frozen=True)
class NameAlignment:
    """A one-to-one matching between candidate and reference territory ids."""

    matched: dict[int, int] = field(default_factory=dict)
    scores: dict[int, float] = field(default_factory=dict)
    unmatched_candidate: tuple[int, ...] = ()
    unmatched_reference: tuple[int, ...] = ()

    @property
    def exact(self) -> int:
        return sum(1 for s in self.scores.values() if s == 1.0)


def align_names(
    candidate: dict[int, str],
    reference: dict[int, str],
    threshold: float = 0.72,
) -> NameAlignment:
    """Match candidate territory ids to reference ids by name.

    Exact normalized matches are taken first, then remaining pairs are matched
    greedily by descending similarity, never reusing either side, and never
    below ``threshold``.  Anything left over is reported unmatched in both
    directions rather than forced into a pairing.
    """
    cand_norm = {cid: normalize_name(n) for cid, n in candidate.items()}
    ref_norm = {rid: normalize_name(n) for rid, n in reference.items()}

    matched: dict[int, int] = {}
    scores: dict[int, float] = {}

    # Pass 1: exact normalized matches.  A normalized name appearing twice on
    # either side is ambiguous, so it is left to the fuzzy pass.
    ref_by_norm: dict[str, list[int]] = {}
    for rid, norm in ref_norm.items():
        ref_by_norm.setdefault(norm, []).append(rid)
    cand_by_norm: dict[str, list[int]] = {}
    for cid, norm in cand_norm.items():
        cand_by_norm.setdefault(norm, []).append(cid)

    for norm, cids in cand_by_norm.items():
        rids = ref_by_norm.get(norm, [])
        if len(cids) == 1 and len(rids) == 1:
            matched[cids[0]] = rids[0]
            scores[cids[0]] = 1.0

    # Pass 2: greedy fuzzy over what remains.
    free_cand = [cid for cid in candidate if cid not in matched]
    taken_ref = set(matched.values())
    free_ref = [rid for rid in reference if rid not in taken_ref]

    pairs = [
        (name_similarity(cand_norm[cid], ref_norm[rid]), cid, rid)
        for cid in free_cand
        for rid in free_ref
    ]
    pairs.sort(key=lambda p: (-p[0], p[1], p[2]))
    used_cand: set[int] = set()
    used_ref: set[int] = set()
    for score, cid, rid in pairs:
        if score < threshold:
            break
        if cid in used_cand or rid in used_ref:
            continue
        matched[cid] = rid
        scores[cid] = score
        used_cand.add(cid)
        used_ref.add(rid)

    return NameAlignment(
        matched=matched,
        scores=scores,
        unmatched_candidate=tuple(sorted(c for c in candidate if c not in matched)),
        unmatched_reference=tuple(
            sorted(r for r in reference if r not in set(matched.values()))
        ),
    )


@dataclass(frozen=True)
class EdgeScore:
    """Edge agreement in the aligned id space.

    ``unscorable`` counts candidate edges touching a territory that could not
    be aligned: those say nothing about graph quality either way, and are kept
    separate so a name failure never inflates or deflates precision.
    """

    true_positive: int
    false_positive: int
    false_negative: int
    unscorable: int
    missing: tuple[tuple[int, int], ...] = ()
    spurious: tuple[tuple[int, int], ...] = ()

    @property
    def precision(self) -> float | None:
        denom = self.true_positive + self.false_positive
        return self.true_positive / denom if denom else None

    @property
    def recall(self) -> float | None:
        denom = self.true_positive + self.false_negative
        return self.true_positive / denom if denom else None

    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p and r else None


def _undirected(pairs) -> set[tuple[int, int]]:
    return {(min(a, b), max(a, b)) for a, b in pairs if a != b}


def score_edges(
    candidate_edges,
    reference_edges,
    alignment: NameAlignment,
) -> EdgeScore:
    """Compare candidate edges to reference edges through a name alignment.

    Reference edges whose endpoints were both aligned are the scorable set; a
    reference edge touching an unaligned territory cannot be found by the
    candidate under any naming, so it is excluded from recall rather than
    charged as a miss.
    """
    mapped: set[tuple[int, int]] = set()
    unscorable = 0
    for a, b in _undirected(candidate_edges):
        ra, rb = alignment.matched.get(a), alignment.matched.get(b)
        if ra is None or rb is None:
            unscorable += 1
            continue
        mapped.add((min(ra, rb), max(ra, rb)))

    aligned_ref_ids = set(alignment.matched.values())
    reference = {
        e
        for e in _undirected(reference_edges)
        if e[0] in aligned_ref_ids and e[1] in aligned_ref_ids
    }

    tp = mapped & reference
    fp = mapped - reference
    fn = reference - mapped
    return EdgeScore(
        true_positive=len(tp),
        false_positive=len(fp),
        false_negative=len(fn),
        unscorable=unscorable,
        missing=tuple(sorted(fn)),
        spurious=tuple(sorted(fp)),
    )


def score_positions(
    candidate_points: dict[int, tuple[float, float]],
    reference_points: dict[int, tuple[float, float]],
    alignment: NameAlignment,
) -> dict:
    """Distances from candidate nodes to reference anchors, in pixels.

    Reported for information only.  Plan v4 states nothing derives from node
    coordinates, and D12's anchors are render centroids rather than printed
    label positions, so a large distance is not by itself an error.
    """
    distances = []
    for cid, rid in sorted(alignment.matched.items()):
        if cid not in candidate_points or rid not in reference_points:
            continue
        cx, cy = candidate_points[cid]
        rx, ry = reference_points[rid]
        distances.append(((cx - rx) ** 2 + (cy - ry) ** 2) ** 0.5)
    if not distances:
        return {"n": 0, "median_px": None, "max_px": None}
    ordered = sorted(distances)
    mid = len(ordered) // 2
    median = (
        ordered[mid]
        if len(ordered) % 2
        else (ordered[mid - 1] + ordered[mid]) / 2
    )
    return {
        "n": len(ordered),
        "median_px": round(median, 1),
        "max_px": round(ordered[-1], 1),
    }
