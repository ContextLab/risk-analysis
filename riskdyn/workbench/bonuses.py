"""bonuses.json: bonus structures that can hold what maps actually print.

Designed against the plan-v3 review's counterexamples (maps 77/100), the
schema must represent -- without forcing a wrong link:

- per-region bonus values (map 1: six continents at 5/5/7/2/3/2);
- bonus numerals printed with NO adjacent region name (map 100), where the
  association is by colour only: ``kind: "region"`` with ``region_id:
  null``, ``association: "by-colour"``, ``status: "needs_review"``;
- per-territory or per-strait ``+1`` markers (map 100): ``kind:
  "territory"`` / ``kind: "edge"``;
- territories outside every region (map 100's grey territories) and maps
  with no regions at all (map 77, ``num_regions=0``): territory
  ``region_id`` may be null and ``regions`` may be empty;
- prose rules stored VERBATIM with their bounding box and never parsed
  into game logic: ``kind: "prose"`` may carry no structured application
  fields at all.
"""
from __future__ import annotations

from typing import Any

VALID_KINDS = {"region", "territory", "edge", "prose"}
VALID_STATUS = {"resolved", "needs_review"}
VALID_ASSOCIATION = {"explicit-label", "by-colour", "by-position", "unresolved"}
VALID_CONFIDENCE = {"high", "medium", "low"}


def validate_bonus_entry(
    entry: dict[str, Any],
    region_ids: set[int],
    territory_ids: set[int],
    edges: set[tuple[int, int]] | None = None,
) -> list[str]:
    """Return a list of problems (empty = valid). Never mutates."""
    problems: list[str] = []
    kind = entry.get("kind")
    if kind not in VALID_KINDS:
        return [f"kind must be one of {sorted(VALID_KINDS)}, got {kind!r}"]
    status = entry.get("status")
    if status not in VALID_STATUS:
        problems.append(f"status must be one of {sorted(VALID_STATUS)}, got {status!r}")
    if entry.get("confidence") not in VALID_CONFIDENCE:
        problems.append(f"confidence must be one of {sorted(VALID_CONFIDENCE)}")
    bbox = entry.get("bbox")
    if bbox is not None and (len(bbox) != 4 or any(not isinstance(v, (int, float)) for v in bbox)):
        problems.append("bbox must be [x, y, w, h] or null")

    if kind == "prose":
        text = entry.get("text_verbatim")
        if not text or not str(text).strip():
            problems.append("prose entry requires non-empty text_verbatim")
        for forbidden in ("region_id", "territory_id", "edge"):
            if entry.get(forbidden) is not None:
                problems.append(
                    f"prose entry must not carry structured field {forbidden!r}: "
                    "prose is stored verbatim, never parsed into game logic"
                )
        if entry.get("value") is not None:
            problems.append("prose entry must keep value null (no parsed interpretation)")
        return problems

    value = entry.get("value")
    if value is not None and not isinstance(value, int):
        problems.append(f"value must be an int or null, got {value!r}")

    if kind == "region":
        rid = entry.get("region_id")
        assoc = entry.get("association")
        if assoc not in VALID_ASSOCIATION:
            problems.append(
                f"region entry association must be one of {sorted(VALID_ASSOCIATION)}"
            )
        if rid is None:
            if status != "needs_review":
                problems.append(
                    "region entry with region_id null (unresolved association) "
                    "must have status needs_review -- never force a wrong link"
                )
        elif rid not in region_ids:
            problems.append(f"region_id {rid} not among defined regions {sorted(region_ids)}")
    elif kind == "territory":
        tid = entry.get("territory_id")
        if tid is None or tid not in territory_ids:
            problems.append(f"territory_id {tid!r} not a known territory")
    elif kind == "edge":
        e = entry.get("edge")
        if not e or len(e) != 2:
            problems.append("edge entry requires edge: [a, b]")
        elif edges is not None:
            key = (min(e), max(e))
            if key not in edges:
                problems.append(f"edge {list(key)} not present in graph.json edges")
    return problems


def validate_bonuses(
    doc: dict[str, Any],
    territory_ids: set[int],
    edges: set[tuple[int, int]] | None = None,
) -> list[str]:
    """Validate a whole bonuses.json document; returns problems."""
    problems: list[str] = []
    region_ids = {r["region_id"] for r in doc.get("regions", [])}
    seen_members: set[int] = set()
    for r in doc.get("regions", []):
        for tid in r.get("territory_ids", []):
            if tid not in territory_ids:
                problems.append(f"region {r['region_id']} lists unknown territory {tid}")
            if tid in seen_members:
                problems.append(f"territory {tid} assigned to more than one region")
            seen_members.add(tid)
    for i, entry in enumerate(doc.get("bonuses", [])):
        for p in validate_bonus_entry(entry, region_ids, territory_ids, edges):
            problems.append(f"bonuses[{i}]: {p}")
    return problems
