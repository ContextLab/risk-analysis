"""Provenance and verification state carried by every workbench artifact.

Every artifact records WHAT produced it, WHEN, and WHETHER a human has
confirmed it.  Nothing here ever flips ``human_verified`` implicitly: a
script finishing is not verification, and an agent's vision pass is not a
human.  The only way an artifact becomes human-verified is an explicit
``verification`` entry in the map's authored annotations naming the person.
"""
from __future__ import annotations

import datetime
from typing import Any

SCHEMA_NOTE = (
    "human_verified is set ONLY from an explicit verification entry in "
    "annotations.json naming a person; generators always write false"
)


def utc_now() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
    )


def make_provenance(
    produced_by: str,
    method: str,
    inputs: list[str] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """A fresh, UNVERIFIED provenance block.

    Args:
        produced_by: the actor, e.g. ``"riskdyn.workbench.build"``,
            ``"claude-agent vision pass"``, ``"human:<name>"``.
        method: how, e.g. ``"sam+seed-selection+annotations"``,
            ``"transcribed from artwork legend"``.
        inputs: repo-relative paths or descriptions of the inputs used.
    """
    block: dict[str, Any] = {
        "produced_by": produced_by,
        "method": method,
        "produced_utc": utc_now(),
        "inputs": list(inputs or []),
        "human_verified": False,
        "verified_by": None,
        "verified_utc": None,
    }
    if note:
        block["note"] = note
    return block


def apply_verification(block: dict[str, Any], verification: dict | None) -> dict[str, Any]:
    """Stamp a provenance block from an authored verification entry.

    ``verification`` comes from annotations.json, e.g.
    ``{"verified": true, "by": "Jeremy Manning", "at": "2026-08-11", "scope": "overlay"}``.
    A missing/false entry leaves the block unverified.  ``by`` must name a
    person; entries whose ``by`` is empty or names an agent are refused so a
    script (or an LLM run) can never launder itself into a human sign-off.
    """
    out = dict(block)
    if not verification or not verification.get("verified"):
        return out
    by = str(verification.get("by") or "").strip()
    if not by or "agent" in by.lower() or by.lower().startswith("claude"):
        raise ValueError(
            f"verification 'by' must name a human, got {by!r} ({SCHEMA_NOTE})"
        )
    out["human_verified"] = True
    out["verified_by"] = by
    out["verified_utc"] = str(verification.get("at") or utc_now())
    return out
