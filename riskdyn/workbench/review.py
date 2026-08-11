"""Local review UI for the human sign-off that closes issue #4's loop.

Topology for all maps is exact (D12 markup); what remains cannot be
automated and this tool exists to make Jeremy's decisions fast:

- criterion d (bonuses) is unverified everywhere: no ground truth exists,
  so a human must compare each legend-read bonus value against the artwork
- region conflicts (legend read vs independent colour clusters, written by
  ``merge_legend`` to ``region_conflicts.json``) need adjudication
- the graph overlays need a "looks right" pass

Decisions POST to ``/api/decision`` and are written into
``data/authored/maps/<id>/annotations.json`` under ``verification``:

- ``bonuses_confirmed``: per-region confirms accumulate in
  ``region_ids_confirmed``; value corrections append to ``corrections``
  AND update the v3 legend file (so a re-merge does not undo them).  When
  every displayed region is decided, ``verified: true`` is recorded with
  ``by``, ``at`` and ``payload_sha256`` -- the hash of the exact bonus
  data confirmed.  ``graph_build._crit_d`` recomputes that hash on every
  build: a mismatch means the data changed after approval, the sign-off
  is STALE, and the map reports unverified again.
- ``conflicts_adjudicated``: one ruling per territory
  (legend | colour | other).
- ``overlay_confirmed``: the graph-overlay verdict.

Only a human action through this UI sets ``verified: true``; the server
refuses to record anything without a ``--by`` identity, and refuses an
identity that ``graph_build._human_signoff`` would never count (agents).

All writes are atomic (tempfile + ``os.replace``) and never drop
unrelated keys.  Crops are generated in memory and served as PNG; they
are never written under ``data/``.  The server binds 127.0.0.1 ONLY: the
map artwork is D12's property and must never be exposed.

CLI:
    ./.venv/bin/python -m riskdyn.workbench.review [--port 8765]
        [--by "Jeremy Manning"] [--open]
"""
from __future__ import annotations

import argparse
import datetime
import html
import io
import json
import os
import pathlib
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from riskdyn.segment import catalog as cat
from riskdyn.workbench.build import AUTHORED_ROOT, PROCESSED_ROOT, _bonuses_doc
from riskdyn.workbench.graph_build import (
    _crit_b,
    _crit_d,
    _crit_e,
    _crit_f,
    _edge_records,
    _human_signoff,
    _summary_or_fallback,
    bonus_payload,
    bonus_payload_sha256,
    load_annotations_v2,
    payload_sha256,
)
from riskdyn.workbench.legend_schema import legend_path, validate_legend_v3

RULINGS = {"legend", "colour", "other"}


class ReviewError(Exception):
    """An HTTP-facing refusal; ``status`` says how loud."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _atomic_write_json(path: pathlib.Path, doc: dict) -> None:
    """tempfile + os.replace in the same directory; indent=1 to match the
    style every authored file already uses, so untouched keys stay
    byte-identical."""
    text = json.dumps(doc, indent=1)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# the app: all data access + decision writing
# --------------------------------------------------------------------------

class ReviewApp:
    def __init__(
        self,
        reviewer: str | None = None,
        authored_root: pathlib.Path | None = None,
        processed_root: pathlib.Path | None = None,
    ):
        self.reviewer = (reviewer or "").strip() or None
        self.authored = pathlib.Path(authored_root or AUTHORED_ROOT)
        self.processed = pathlib.Path(processed_root or PROCESSED_ROOT)
        self._summaries: dict[int, object] = {}
        self._lock = threading.Lock()

    # -- loading ----------------------------------------------------------

    def map_ids(self) -> list[int]:
        return sorted(
            int(p.name)
            for p in self.authored.iterdir()
            if p.name.isdigit() and (p / "annotations.json").is_file()
        )

    def ann_path(self, map_id: int) -> pathlib.Path:
        return self.authored / str(map_id) / "annotations.json"

    def load_ann(self, map_id: int) -> dict:
        return load_annotations_v2(self.ann_path(map_id), map_id)

    def legend_file(self, map_id: int) -> pathlib.Path:
        return legend_path(map_id, self.authored)

    def load_legend(self, map_id: int) -> dict | None:
        p = self.legend_file(map_id)
        return json.loads(p.read_text()) if p.is_file() else None

    def load_conflicts(self, map_id: int) -> dict | None:
        p = self.processed / str(map_id) / "region_conflicts.json"
        return json.loads(p.read_text()) if p.is_file() else None

    def load_sample(self, map_id: int) -> dict | None:
        p = self.processed / str(map_id) / "region_sample.json"
        return json.loads(p.read_text()) if p.is_file() else None

    def summary(self, map_id: int):
        if map_id not in self._summaries:
            self._summaries[map_id] = _summary_or_fallback(map_id)
        return self._summaries[map_id]

    # -- bonus rows: what the human confirms ------------------------------

    def bonus_rows(
        self, map_id: int, ann: dict | None = None, legend: dict | None = None
    ) -> tuple[list[dict], str]:
        """(rows, source) for the bonus section.

        Merged annotations regions win; otherwise a not-yet-merged legend
        read is shown (map 100's merge is refused until its conflicts are
        adjudicated, but its bonus VALUES are reviewable now, and the
        sign-off survives the eventual merge because the merged values are
        the same values).  Empty rows mean nothing to confirm.
        """
        ann = ann if ann is not None else self.load_ann(map_id)
        if ann.get("regions"):
            rows = [
                {
                    "region_id": r["region_id"],
                    "name": r.get("name"),
                    "name_provenance": r.get("name_provenance"),
                    "bonus": r.get("bonus"),
                    "bonus_text_verbatim": r.get("bonus_text_verbatim"),
                    "bonus_bbox": r.get("bonus_bbox"),
                    "confidence": r.get("confidence"),
                }
                for r in ann["regions"]
            ]
            return rows, "annotations (merged legend read)"
        legend = legend if legend is not None else self.load_legend(map_id)
        if legend and legend.get("regions"):
            rows = [
                {
                    "region_id": r["region_id"],
                    "name": r["name"].get("text"),
                    "name_provenance": r["name"].get("provenance"),
                    "bonus": r["bonus"].get("value"),
                    "bonus_text_verbatim": r["bonus"].get("text_verbatim"),
                    "bonus_bbox": r["bonus"].get("bbox"),
                    "confidence": r["bonus"].get("confidence"),
                }
                for r in legend["regions"]
            ]
            return rows, "legend read (not yet merged into annotations)"
        return [], ""

    def display_payload_sha(self, map_id: int, ann: dict | None = None) -> str:
        """Hash of the exact bonus data the bonus section displays."""
        ann = ann if ann is not None else self.load_ann(map_id)
        rows, _ = self.bonus_rows(map_id, ann)
        return payload_sha256(
            bonus_payload(
                rows, ann.get("extra_bonuses"), ann.get("special_rules")
            )
        )

    # -- conflicts: decisions vs cluster-quality warnings ------------------

    @staticmethod
    def split_conflicts(conflicts: dict | None) -> tuple[list[dict], list[dict]]:
        """(decisions, warnings), most actionable first.

        A flagged entry is a DECISION only when the legend and the colour
        majority disagree about which region the territory is in.  When
        both resolve to the SAME region (the singleton-cluster artifacts:
        the flag is a cluster-quality observation, not a membership
        dispute) it is a WARNING: still listed -- silently dropping it
        would hide a real signal about sampling quality -- but there is
        nothing to rule on, so no ruling is asked for.

        Decisions sort likely-colour-method-errors first: an unreliable
        sample or low patch_consistency means the colour side is the
        suspect one, and that class is common and fast to dispatch.

        The merge gate itself is deliberately UNCHANGED: merge_legend
        still refuses on every flagged entry, warnings included --
        refusing on a cluster-quality warning is correct for an automated
        merge; this split is purely about not demanding a human ruling
        where there is no dispute.
        """
        entries = (conflicts or {}).get("conflicts", [])
        decisions = [
            c for c in entries
            if c["legend_region_id"] != c["cluster_majority_region_id"]
        ]
        warnings = [
            c for c in entries
            if c["legend_region_id"] == c["cluster_majority_region_id"]
        ]
        decisions.sort(
            key=lambda c: (
                c.get("sample_reliable") is not False,  # unreliable first
                c.get("patch_consistency")
                if c.get("patch_consistency") is not None
                else 1.0,
                c["name"],
            )
        )
        return decisions, warnings

    # -- per-map status for the index -------------------------------------

    def signoff_state(self, map_id: int, ann: dict | None = None) -> dict:
        ann = ann if ann is not None else self.load_ann(map_id)
        bc = (ann.get("verification") or {}).get("bonuses_confirmed") or {}
        rows, _ = self.bonus_rows(map_id, ann)
        decided = set(bc.get("region_ids_confirmed") or []) | {
            c["region_id"] for c in bc.get("corrections") or []
        }
        signed = _human_signoff(bc)
        stale = signed and bc.get("payload_sha256") != self.display_payload_sha(
            map_id, ann
        )
        return {
            "signed": signed and not stale,
            "stale": stale,
            "by": bc.get("by"),
            "at": bc.get("at"),
            "n_decided": len(decided & {r["region_id"] for r in rows}),
            "n_rows": len(rows),
        }

    def map_status(self, map_id: int) -> dict:
        ann = self.load_ann(map_id)
        summary = self.summary(map_id)
        territories = sorted(ann["territories"], key=lambda t: t["territory_id"])
        edges = _edge_records(ann)
        bonuses_doc = _bonuses_doc(ann, [], map_id)
        bonuses_doc["special_rules"] = ann.get("special_rules", [])
        verification = ann.get("verification", {})
        crit = {
            "b": _crit_b(territories, summary.num_territories),
            "e": _crit_e(edges),
            "f": _crit_f(territories, ann.get("regions", []), summary.num_regions),
            "d": _crit_d(
                bonuses_doc, ann.get("regions", []), verification,
                bonus_payload_sha256(ann),
            ),
        }
        conflicts = self.load_conflicts(map_id)
        n_conf = conflicts["n_conflicts"] if conflicts else 0
        decisions, warnings = self.split_conflicts(conflicts)
        adjudicated = {
            e.get("territory")
            for e in (verification.get("conflicts_adjudicated") or [])
        }
        open_conf = sum(1 for c in decisions if c["name"] not in adjudicated)
        rows, source = self.bonus_rows(map_id, ann)
        legend_file_exists = self.legend_file(map_id).is_file()
        signoff = self.signoff_state(map_id, ann)
        needs = open_conf > 0 or (bool(rows) and not signoff["signed"])
        overlay_conf = (verification.get("overlay_confirmed") or {})
        return {
            "map_id": map_id,
            "name": summary.name,
            "n_territories": len(territories),
            "n_edges": len(edges),
            "criteria": {k: v["status"] for k, v in crit.items()},
            "stale": crit["d"].get("stale_signoff", False) or signoff["stale"],
            "n_conflicts": n_conf,
            "n_decisions": len(decisions),
            "n_warnings": len(warnings),
            "open_conflicts": open_conf,
            "has_legend": bool(rows),
            "legend_file_exists": legend_file_exists,
            "bonus_source": source,
            "signoff": signoff,
            "overlay_verified": _human_signoff(overlay_conf),
            "needs_you": needs,
        }

    def index_rows(self) -> list[dict]:
        rows = [self.map_status(m) for m in self.map_ids()]

        def priority(r: dict) -> tuple:
            if r["open_conflicts"] > 0:
                group = 0
            elif r["has_legend"] and not r["signoff"]["signed"]:
                group = 1
            else:
                group = 2
            return (group, r["map_id"])

        return sorted(rows, key=priority)

    # -- crops (in-memory PNG; never written under data/) ------------------

    def _artwork(self, map_id: int):
        from PIL import Image

        return Image.open(cat.image_path(map_id)).convert("RGB")

    def _crop_png(
        self, map_id: int, box: tuple[float, float, float, float],
        scale: float, margin: int = 0,
    ) -> bytes:
        from PIL import Image

        x, y, w, h = box
        with self._artwork(map_id) as im:
            left = max(0, int(x) - margin)
            top = max(0, int(y) - margin)
            right = min(im.width, int(x + w) + margin)
            bottom = min(im.height, int(y + h) + margin)
            if right <= left or bottom <= top:
                raise ReviewError(404, f"bbox {box} is outside the artwork")
            crop = im.crop((left, top, right, bottom))
            if scale != 1:
                crop = crop.resize(
                    (round(crop.width * scale), round(crop.height * scale)),
                    Image.NEAREST,  # digits stay crisp
                )
            buf = io.BytesIO()
            crop.save(buf, format="PNG")
            return buf.getvalue()

    def crop_bonus(self, map_id: int, region_id: int) -> bytes:
        rows, _ = self.bonus_rows(map_id)
        row = next((r for r in rows if r["region_id"] == region_id), None)
        if row is None:
            raise ReviewError(404, f"map {map_id} has no region {region_id}")
        if not row["bonus_bbox"]:
            raise ReviewError(404, f"region {region_id} has no bonus_bbox")
        return self._crop_png(map_id, row["bonus_bbox"], scale=3, margin=3)

    def legend_bbox_union(self, map_id: int) -> list[float] | None:
        legend = self.load_legend(map_id)
        boxes = [
            lb["bbox"] for lb in (legend or {}).get("legend_bboxes", [])
            if lb.get("bbox")
        ]
        if not boxes:
            return None
        x0 = min(b[0] for b in boxes)
        y0 = min(b[1] for b in boxes)
        x1 = max(b[0] + b[2] for b in boxes)
        y1 = max(b[1] + b[3] for b in boxes)
        return [x0, y0, x1 - x0, y1 - y0]

    def crop_legend(self, map_id: int) -> bytes:
        box = self.legend_bbox_union(map_id)
        if box is None:
            raise ReviewError(
                404, f"map {map_id} has no legend read with legend_bboxes"
            )
        return self._crop_png(map_id, box, scale=1, margin=4)

    def crop_territory(self, map_id: int, territory_id: int, size: int = 240) -> bytes:
        ann = self.load_ann(map_id)
        t = next(
            (t for t in ann["territories"] if t["territory_id"] == territory_id),
            None,
        )
        if t is None:
            raise ReviewError(404, f"map {map_id} has no territory {territory_id}")
        # D12 coordinates are the label-box TOP-LEFT, not a centre; the
        # point inside the territory is the box centre (see
        # riskdyn.workbench.regions.LABEL_BOX_OFFSET).
        cx, cy = t["x"] + 15, t["y"] + 10
        half = size // 2
        return self._crop_png(map_id, (cx - half, cy - half, size, size), scale=1)

    def processed_png(self, map_id: int, name: str) -> bytes:
        if name not in ("overlay.png", "region_overlay.png"):
            raise ReviewError(404, f"not served: {name}")
        p = self.processed / str(map_id) / name
        if not p.is_file():
            raise ReviewError(404, f"{p} has not been built")
        return p.read_bytes()

    # -- decisions ---------------------------------------------------------

    def record_decision(self, body: dict) -> dict:
        if not self.reviewer:
            raise ReviewError(
                403,
                "refusing to record: no sign-off identity is set (restart "
                'with --by "Your Name")',
            )
        if not _human_signoff({"verified": True, "by": self.reviewer}):
            raise ReviewError(
                403,
                f"refusing to record: identity {self.reviewer!r} would never "
                "count as a HUMAN sign-off (agents cannot sign off)",
            )
        try:
            map_id = int(body["map_id"])
            kind = body["kind"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ReviewError(400, f"bad decision payload: {exc}") from exc
        if map_id not in self.map_ids():
            raise ReviewError(404, f"unknown map {map_id}")
        with self._lock:
            if kind == "bonus_confirm":
                return self._bonus_confirm(map_id, body)
            if kind == "bonus_wrong":
                return self._bonus_wrong(map_id, body)
            if kind == "conflict":
                return self._conflict(map_id, body)
            if kind == "overlay":
                return self._overlay(map_id, body)
        raise ReviewError(400, f"unknown decision kind {kind!r}")

    def _bc(self, ann: dict) -> dict:
        verification = ann.setdefault("verification", {})
        return verification.setdefault("bonuses_confirmed", {})

    def _finalize_bonuses(self, map_id: int, ann: dict) -> dict:
        """verified: true iff every displayed region is decided; the hash
        covers the data as it stands NOW (post-correction)."""
        bc = self._bc(ann)
        rows, _ = self.bonus_rows(map_id, ann)
        all_ids = {r["region_id"] for r in rows}
        decided = set(bc.get("region_ids_confirmed") or []) | {
            c["region_id"] for c in bc.get("corrections") or []
        }
        if all_ids and all_ids <= decided:
            bc["verified"] = True
            bc["by"] = self.reviewer
            bc["at"] = _now()
            bc["payload_sha256"] = self.display_payload_sha(map_id, ann)
        else:
            bc["verified"] = False
        return {
            "verified": bc["verified"],
            "n_decided": len(decided & all_ids),
            "n_rows": len(all_ids),
        }

    def _bonus_confirm(self, map_id: int, body: dict) -> dict:
        region_id = int(body["region_id"])
        ann = self.load_ann(map_id)
        rows, _ = self.bonus_rows(map_id, ann)
        if region_id not in {r["region_id"] for r in rows}:
            raise ReviewError(404, f"map {map_id} has no bonus region {region_id}")
        bc = self._bc(ann)
        confirmed = bc.setdefault("region_ids_confirmed", [])
        if region_id not in confirmed:
            confirmed.append(region_id)
            confirmed.sort()
        state = self._finalize_bonuses(map_id, ann)
        _atomic_write_json(self.ann_path(map_id), ann)
        return {"ok": True, "recorded": f"region {region_id} confirmed", **state}

    def _bonus_wrong(self, map_id: int, body: dict) -> dict:
        region_id = int(body["region_id"])
        try:
            now_value = int(body["value"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ReviewError(
                400, f"a correction needs an integer bonus value: {exc}"
            ) from exc
        note = str(body.get("note") or "")
        ann = self.load_ann(map_id)
        rows, _ = self.bonus_rows(map_id, ann)
        row = next((r for r in rows if r["region_id"] == region_id), None)
        if row is None:
            raise ReviewError(404, f"map {map_id} has no bonus region {region_id}")
        was = row["bonus"]

        # -- the legend file first: corrections must survive a re-merge
        legend = self.load_legend(map_id)
        if legend:
            lr = next(
                (r for r in legend.get("regions", [])
                 if r.get("region_id") == region_id),
                None,
            )
            if lr is not None:
                lr["bonus"]["value"] = now_value
                lr["bonus"]["text_verbatim"] = str(now_value)
                lr["bonus"]["confidence"] = "high"
                validate_legend_v3(legend)  # write valid v3 or not at all
                _atomic_write_json(self.legend_file(map_id), legend)

        # -- merged annotations regions, if present
        for r in ann.get("regions", []):
            if r["region_id"] == region_id:
                r["bonus"] = now_value
                r["bonus_text_verbatim"] = str(now_value)
                r["confidence"] = "high"

        bc = self._bc(ann)
        bc.setdefault("corrections", []).append(
            {
                "region_id": region_id,
                "was": was,
                "now": now_value,
                "note": note,
                "by": self.reviewer,
                "at": _now(),
            }
        )
        state = self._finalize_bonuses(map_id, ann)
        _atomic_write_json(self.ann_path(map_id), ann)
        return {
            "ok": True,
            "recorded": f"region {region_id}: {was} -> {now_value}",
            **state,
        }

    def _conflict(self, map_id: int, body: dict) -> dict:
        territory = str(body.get("territory") or "")
        ruling = body.get("ruling")
        if ruling not in RULINGS:
            raise ReviewError(400, f"ruling must be one of {sorted(RULINGS)}")
        conflicts = self.load_conflicts(map_id)
        record = next(
            (c for c in (conflicts or {}).get("conflicts", [])
             if c["name"] == territory),
            None,
        )
        if record is None:
            raise ReviewError(
                404, f"map {map_id} has no open conflict for {territory!r}"
            )
        if record["legend_region_id"] == record["cluster_majority_region_id"]:
            raise ReviewError(
                400,
                f"{territory!r} is a cluster-quality warning, not a "
                "disagreement: the legend and the colour majority both "
                f"resolve to region {record['legend_region_id']}, so there "
                "is nothing to rule on",
            )
        if ruling == "legend":
            region_id = record["legend_region_id"]
        elif ruling == "colour":
            region_id = record["cluster_majority_region_id"]
        else:
            region_id = body.get("region_id")
            if region_id is not None:
                region_id = int(region_id)
        entry = {
            "territory": territory,
            "ruling": ruling,
            "region_id": region_id,
            "by": self.reviewer,
            "at": _now(),
            "note": str(body.get("note") or ""),
        }
        if ruling == "other" and body.get("region"):
            entry["region"] = str(body["region"])
        ann = self.load_ann(map_id)
        verification = ann.setdefault("verification", {})
        adjudicated = verification.setdefault("conflicts_adjudicated", [])
        adjudicated[:] = [
            e for e in adjudicated if e.get("territory") != territory
        ]
        adjudicated.append(entry)
        _atomic_write_json(self.ann_path(map_id), ann)
        return {"ok": True, "recorded": f"{territory}: {ruling}"}

    def _overlay(self, map_id: int, body: dict) -> dict:
        ok = bool(body.get("looks_right"))
        ann = self.load_ann(map_id)
        verification = ann.setdefault("verification", {})
        verification["overlay_confirmed"] = {
            "verified": ok,
            "by": self.reviewer,
            "at": _now(),
            "note": str(body.get("note") or ""),
        }
        _atomic_write_json(self.ann_path(map_id), ann)
        return {"ok": True, "recorded": "looks right" if ok else "problem noted"}


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------

_CSS = """
body{font-family:system-ui,sans-serif;background:#14161a;color:#e8e8e8;
     margin:0;padding:1rem 2rem}
a{color:#7fc4ff}
h1{font-size:1.3rem}h2{font-size:1.1rem;border-bottom:1px solid #333;
     padding-bottom:.3rem;margin-top:2rem}
table{border-collapse:collapse;width:100%;font-size:.9rem}
th,td{padding:.25rem .55rem;text-align:left;border-bottom:1px solid #2a2d33}
th{cursor:pointer;user-select:none;color:#9ab;position:sticky;top:0;
   background:#14161a}
tbody tr{cursor:pointer}
tbody tr:hover{background:#1e2128}
.pass{color:#5fd38a}.fail{color:#ff6b6b}.unverified{color:#e0b25f}
.stale{color:#ff9f43;font-weight:600}
.needs{color:#ff9f43;font-weight:700}
.item{display:flex;gap:1rem;align-items:flex-start;border:1px solid #2a2d33;
      border-radius:6px;padding:.6rem;margin:.5rem 0;background:#191c21}
.item.cur{outline:2px solid #7fc4ff}
.item.done{opacity:.55}
.item img{image-rendering:pixelated;border:1px solid #444;background:#fff;
          max-width:420px}
.item .meta{flex:1;min-width:14rem}
.swatch{display:inline-block;width:14px;height:14px;border:1px solid #888;
        vertical-align:-2px;margin-right:4px}
button{background:#2b3a4d;color:#e8e8e8;border:1px solid #46617e;
       border-radius:4px;padding:.25rem .7rem;margin-right:.35rem;
       cursor:pointer}
button.yes{background:#1e4630;border-color:#2f7a4f}
button.no{background:#4c2626;border-color:#8a4040}
input{background:#0f1114;color:#e8e8e8;border:1px solid #444;
      border-radius:3px;padding:.2rem .4rem}
.banner{padding:.5rem .8rem;border-radius:6px;margin:.5rem 0}
.banner.ok{background:#173322;border:1px solid #2f7a4f}
.banner.warn{background:#3d2a12;border:1px solid #a06a1f}
.banner.plain{background:#20242b;border:1px solid #3a3f47}
.badge{background:#5a3d13;color:#ffce7a;border-radius:4px;padding:0 .4rem;
       font-size:.78rem;margin-left:.4rem}
.result{color:#5fd38a;margin-left:.6rem;font-size:.85rem}
#help{display:none;position:fixed;right:1rem;bottom:1rem;background:#20242b;
      border:1px solid #46617e;border-radius:8px;padding:1rem;z-index:9}
.warnitem{border:1px dashed #6a5a2a;border-radius:6px;padding:.6rem;
          margin:.5rem 0;background:#1c1a14}
details summary{cursor:pointer;color:#e0b25f;margin:.6rem 0}
.legendctx img{max-width:100%;border:1px solid #444;background:#fff}
.overlaywrap img{max-width:100%;border:1px solid #444}
.small{color:#9aa;font-size:.82rem}
"""

_MAP_JS = """
const items = [...document.querySelectorAll('.item')];
let curIdx = items.findIndex(i => !i.classList.contains('done'));
if (curIdx < 0) curIdx = 0;
function cur(){ return items[curIdx]; }
function setCur(i){
  if (!items.length) return;
  items[curIdx] && items[curIdx].classList.remove('cur');
  curIdx = Math.max(0, Math.min(items.length - 1, i));
  items[curIdx].classList.add('cur');
  items[curIdx].scrollIntoView({block:'center', behavior:'smooth'});
}
function advance(){
  for (let i = curIdx + 1; i < items.length; i++) {
    if (!items[i].classList.contains('done')) { setCur(i); return; }
  }
  for (let i = 0; i < items.length; i++) {
    if (!items[i].classList.contains('done')) { setCur(i); return; }
  }
}
async function post(payload, item, btn){
  const r = await fetch('/api/decision', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify(payload)});
  let res = {};
  try { res = await r.json(); } catch (e) {}
  if (!r.ok) { alert(res.error || ('refused: HTTP ' + r.status)); return; }
  item.classList.add('done');
  const out = item.querySelector('.result');
  if (out) out.textContent = res.recorded || 'recorded';
  if (item.classList.contains('conflict-item')) {
    const pos = document.getElementById('conflict-pos');
    if (pos) pos.textContent =
      document.querySelectorAll('.conflict-item.done').length + ' of ' +
      document.querySelectorAll('.conflict-item').length + ' decided';
  }
  if (res.verified) {
    const b = document.getElementById('bonus-state');
    if (b) b.textContent = 'ALL REGIONS DECIDED — signed off';
  }
  advance();
}
function decide(btn){
  const item = btn.closest('.item');
  const payload = JSON.parse(btn.dataset.d);
  const note = item.querySelector('.note');
  if (note && note.value) payload.note = note.value;
  post(payload, item, btn);
}
function decideWrong(btn){
  const item = btn.closest('.item');
  const payload = JSON.parse(btn.dataset.d);
  const val = item.querySelector('.val');
  const v = parseInt(val && val.value, 10);
  if (isNaN(v)) { val.focus(); val.select(); return; }
  payload.value = v;
  const note = item.querySelector('.note');
  if (note && note.value) payload.note = note.value;
  post(payload, item, btn);
}
function decideOther(btn){
  const item = btn.closest('.item');
  const payload = JSON.parse(btn.dataset.d);
  const region = item.querySelector('.region');
  if (region && region.value) payload.region = region.value;
  const note = item.querySelector('.note');
  if (note && note.value) payload.note = note.value;
  post(payload, item, btn);
}
function toggleHelp(){
  const h = document.getElementById('help');
  h.style.display = h.style.display === 'block' ? 'none' : 'block';
}
document.querySelectorAll('.val').forEach(v => {
  v.addEventListener('keydown', e => {
    if (e.key === 'Enter') {
      e.preventDefault();
      decideWrong(v.closest('.item').querySelector('button.no'));
    }
  });
});
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
    if (e.key === 'Escape') e.target.blur();
    return;
  }
  if (e.key === 'j') setCur(curIdx + 1);
  else if (e.key === 'k') setCur(curIdx - 1);
  else if (e.key === 'y') { const b = cur() && cur().querySelector('button.yes'); if (b) b.click(); }
  else if (e.key === 'n') { const b = cur() && cur().querySelector('button.no'); if (b) b.click(); }
  else if (e.key === 'e') { const v = cur() && cur().querySelector('.val'); if (v) { e.preventDefault(); v.focus(); v.select(); } }
  else if (e.key === 'Enter') { const nm = document.getElementById('next-map'); if (nm) location = nm.href; }
  else if (e.key === '?') toggleHelp();
});
if (items.length) setCur(curIdx);
"""

_INDEX_JS = """
function sortTable(idx){
  const tb = document.querySelector('#maps tbody');
  const rows = [...tb.rows];
  const dir = tb.dataset.sortCol == idx && tb.dataset.sortDir != 'desc'
    ? 'desc' : 'asc';
  tb.dataset.sortCol = idx; tb.dataset.sortDir = dir;
  rows.sort((a, b) => {
    const av = a.cells[idx].dataset.sort, bv = b.cells[idx].dataset.sort;
    const an = parseFloat(av), bn = parseFloat(bv);
    const cmp = (!isNaN(an) && !isNaN(bn)) ? an - bn
      : String(av).localeCompare(String(bv));
    return dir === 'asc' ? cmp : -cmp;
  });
  rows.forEach(r => tb.appendChild(r));
}
"""

_HELP = """
<div id="help">
<b>Keyboard</b><br>
j / k &mdash; next / previous item<br>
y &mdash; confirm ("legend is right" / "looks right")<br>
n &mdash; wrong ("colour is right" / "problem")<br>
e &mdash; edit the value (Enter submits the correction)<br>
Enter &mdash; next map<br>
? &mdash; toggle this help
</div>
"""


def _esc(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _jattr(payload: dict) -> str:
    return html.escape(json.dumps(payload), quote=True)


def _page(title: str, body: str, js: str = "") -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{_esc(title)}</title><style>{_CSS}</style></head>"
        f"<body>{body}{_HELP}<script>{js}</script></body></html>"
    )


def render_index(app: ReviewApp) -> str:
    rows = app.index_rows()
    signed = sum(1 for r in rows if r["signoff"]["signed"])
    open_conf = sum(r["open_conflicts"] for r in rows)
    conf_maps = sum(1 for r in rows if r["open_conflicts"])
    first = next((r for r in rows if r["needs_you"]), None)
    first_link = (
        f"<a href='/map/{first['map_id']}'>first unreviewed: map "
        f"{first['map_id']} {_esc(first['name'])}</a>"
        if first
        else "nothing needs you"
    )
    head = "".join(
        f"<th onclick='sortTable({i})'>{h}</th>"
        for i, h in enumerate(
            ["map", "name", "terr", "edges", "b", "e", "f", "d",
             "conflicts", "bonus rows", "sign-off", "needs you"]
        )
    )
    body_rows = []
    for r in rows:
        crit = "".join(
            f"<td data-sort='{r['criteria'][k]}' class='{r['criteria'][k]}'>"
            f"{r['criteria'][k][0]}</td>"
            for k in ("b", "e", "f", "d")
        )
        so = r["signoff"]
        if so["stale"]:
            so_txt, so_cls = "STALE — data changed since sign-off", "stale"
        elif so["signed"]:
            so_txt, so_cls = f"signed off by {so['by']}", "pass"
        elif so["n_rows"]:
            so_txt, so_cls = f"{so['n_decided']} of {so['n_rows']} decided", "unverified"
        elif r["legend_file_exists"]:
            so_txt, so_cls = "legend read defines no bonus regions", "small"
        else:
            so_txt, so_cls = "no legend read yet", "small"
        # the index counts DECISIONS; same-region warnings are noted small
        conf_parts = []
        if r["n_decisions"]:
            conf_parts.append(
                f"{r['open_conflicts']} open"
                + (
                    f" of {r['n_decisions']}"
                    if r["open_conflicts"] != r["n_decisions"]
                    else ""
                )
            )
        if r["n_warnings"]:
            conf_parts.append(f"+{r['n_warnings']} warn")
        conf_txt = " ".join(conf_parts) if conf_parts else "—"
        body_rows.append(
            f"<tr onclick=\"location='/map/{r['map_id']}'\">"
            f"<td data-sort='{r['map_id']}'>{r['map_id']}</td>"
            f"<td data-sort='{_esc(r['name'])}'>{_esc(r['name'])}</td>"
            f"<td data-sort='{r['n_territories']}'>{r['n_territories']}</td>"
            f"<td data-sort='{r['n_edges']}'>{r['n_edges']}</td>"
            + crit
            + f"<td data-sort='{r['open_conflicts']}'>{conf_txt}</td>"
            f"<td data-sort='{so['n_rows']}'>{so['n_rows'] or '—'}</td>"
            f"<td data-sort='{_esc(so_txt)}' class='{so_cls}'>{_esc(so_txt)}</td>"
            f"<td data-sort='{int(r['needs_you'])}' class='needs'>"
            f"{'YES' if r['needs_you'] else ''}</td></tr>"
        )
    body = (
        "<h1>riskdyn review — human sign-off</h1>"
        f"<p>bonuses signed off: <b>{signed} of {len(rows)}</b> &middot; "
        f"open conflicts: <b>{open_conf}</b> across {conf_maps} map(s) &middot; "
        f"{first_link} &middot; <span class='small'>press ? for keyboard help"
        "</span></p>"
        "<p class='small'>default order: maps with open conflicts first, then "
        "unverified bonuses with a legend read available, then the rest. "
        "Click a column header to sort; click a row to review that map.</p>"
        f"<table id='maps'><thead><tr>{head}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody></table>"
    )
    return _page("riskdyn review", body, _INDEX_JS)


def _bonus_section(app: ReviewApp, map_id: int, ann: dict) -> str:
    rows, source = app.bonus_rows(map_id, ann)
    legend_exists = app.legend_file(map_id).is_file()
    if not rows:
        if legend_exists:
            msg = (
                "A legend read exists for this map but defines no bonus "
                "regions (some legends carry only special rules), so there "
                "are no bonus values to confirm here."
            )
        else:
            msg = (
                f"No legend read exists yet for this map "
                f"(data/authored/maps/{map_id}/legend-map{map_id}.json). "
                "There is nothing to confirm until one is authored — this "
                "is the expected state for most maps, not an error."
            )
        return f"<div class='banner plain'>{_esc(msg)}</div>"

    so = app.signoff_state(map_id, ann)
    if so["stale"]:
        banner = (
            "<div class='banner warn'><b>Data changed since sign-off.</b> "
            f"The bonuses were signed off by {_esc(so['by'])} at "
            f"{_esc(so['at'])}, but the recorded payload hash no longer "
            "matches the data on disk — the sign-off is stale and this map "
            "counts as unverified again. Re-review below.</div>"
        )
    elif so["signed"]:
        banner = (
            f"<div class='banner ok' id='bonus-state'>Signed off by "
            f"{_esc(so['by'])} at {_esc(so['at'])}.</div>"
        )
    else:
        banner = (
            f"<div class='banner plain' id='bonus-state'>{so['n_decided']} of "
            f"{so['n_rows']} regions decided — sign-off is recorded "
            "automatically when the last one is.</div>"
        )
    legend_union = app.legend_bbox_union(map_id)
    ctx = (
        f"<div class='legendctx'><img src='/crop/legend/{map_id}.png' "
        "alt='whole legend'></div>"
        if legend_union
        else "<div class='small'>no legend_bboxes recorded; whole-legend "
        "context crop unavailable</div>"
    )
    bc = (ann.get("verification") or {}).get("bonuses_confirmed") or {}
    decided = set(bc.get("region_ids_confirmed") or []) | {
        c["region_id"] for c in (bc.get("corrections") or [])
    }
    items = []
    for r in rows:
        rid = r["region_id"]
        if r["bonus_bbox"]:
            img = (
                f"<img src='/crop/bonus/{map_id}/{rid}.png' "
                f"alt='bonus crop region {rid}'>"
            )
        elif legend_union:
            img = (
                f"<img src='/crop/legend/{map_id}.png' alt='whole legend'>"
                "<div class='small'>per-region crop unavailable (no "
                "bonus_bbox recorded) — showing the whole legend</div>"
            )
        else:
            img = (
                "<div class='small'>per-region crop unavailable (no "
                "bonus_bbox and no legend_bboxes recorded)</div>"
            )
        done = " done" if rid in decided and not so["stale"] else ""
        items.append(
            f"<div class='item{done}'>"
            f"<div>{img}</div>"
            "<div class='meta'>"
            f"<b>{_esc(r['name']) or '(unnamed)'}</b> "
            f"<span class='small'>region {rid}"
            f"{' · name: ' + _esc(r['name_provenance']) if r['name_provenance'] else ''}"
            "</span><br>"
            f"bonus <b>{_esc(r['bonus'])}</b> &middot; verbatim "
            f"&ldquo;{_esc(r['bonus_text_verbatim'])}&rdquo; &middot; "
            f"confidence {_esc(r['confidence'])}"
            "</div>"
            "<div class='actions'>"
            f"<button class='yes' onclick='decide(this)' "
            f"data-d=\"{_jattr({'kind': 'bonus_confirm', 'map_id': map_id, 'region_id': rid})}\">"
            "confirm (y)</button>"
            f"<button class='no' onclick='decideWrong(this)' "
            f"data-d=\"{_jattr({'kind': 'bonus_wrong', 'map_id': map_id, 'region_id': rid})}\">"
            "wrong (n)</button>"
            f"<input class='val' size='3' value='{_esc(r['bonus'])}' "
            "title='corrected value (e)'> "
            "<input class='note' size='18' placeholder='note'>"
            "<span class='result'></span>"
            "</div></div>"
        )
    extra = ""
    if ann.get("extra_bonuses") or ann.get("special_rules"):
        parts = []
        for eb in ann.get("extra_bonuses", []):
            parts.append(
                f"<li>extra bonus: {_esc(eb.get('description_verbatim') or eb)}"
                f" = {_esc(eb.get('value'))}</li>"
            )
        for sr in ann.get("special_rules", []):
            txt = sr.get("text_verbatim") if isinstance(sr, dict) else sr
            parts.append(f"<li>special rule: {_esc(txt)}</li>")
        extra = (
            "<p class='small'>also covered by this sign-off:</p>"
            f"<ul class='small'>{''.join(parts)}</ul>"
        )
    return (
        f"<p class='small'>source: {_esc(source)}</p>"
        + banner + ctx + "".join(items) + extra
    )


def _conflict_section(app: ReviewApp, map_id: int, ann: dict) -> str:
    conflicts = app.load_conflicts(map_id)
    if not conflicts or not conflicts["conflicts"]:
        return (
            "<div class='banner plain'>No open region conflicts recorded for "
            "this map (data/processed/maps/&lt;id&gt;/region_conflicts.json "
            "is absent or empty).</div>"
        )
    legend = app.load_legend(map_id) or {}
    lregions = {
        r["region_id"]: r for r in legend.get("regions", [])
        if isinstance(r.get("region_id"), int)
    }
    sample = app.load_sample(map_id) or {}
    clusters = {c["cluster_id"]: c for c in sample.get("clusters", [])}
    adjudicated = {
        e.get("territory"): e
        for e in (ann.get("verification", {}).get("conflicts_adjudicated") or [])
    }

    def region_label(rid) -> str:
        if rid is None:
            return "no region"
        lr = lregions.get(rid)
        name = (lr or {}).get("name", {}).get("text") if lr else None
        hexv = (lr or {}).get("colour_hex")
        sw = f"<span class='swatch' style='background:{_esc(hexv)}'></span>" if hexv else ""
        return f"{sw}region {rid}" + (f" ({_esc(name)})" if name else "")

    def meta_html(c: dict) -> str:
        tid = c["territory_id"]
        cid = c["cluster_id"]
        cluster_hex = clusters.get(cid, {}).get("mean_hex")
        csw = (
            f"<span class='swatch' style='background:{_esc(cluster_hex)}'></span>"
            if cluster_hex
            else ""
        )
        singleton = (
            "<span class='badge'>colour-isolated singleton — predictable "
            "artifact</span>"
            if c.get("cluster_size") == 1
            else ""
        )
        return (
            f"<b>{_esc(c['name'])}</b> "
            f"<span class='small'>territory {tid}</span>{singleton}<br>"
            f"legend claims: {region_label(c['legend_region_id'])}<br>"
            f"colour implies: {csw}cluster {cid} &rarr; "
            f"{region_label(c['cluster_majority_region_id'])}"
            f"{' <span class=badge>tied plurality</span>' if c.get('ambiguous_majority') else ''}<br>"
            f"<span class='small'>{_esc(c['reason'])}</span><br>"
            f"<span class='small'>sample_reliable={_esc(c.get('sample_reliable'))} "
            f"&middot; patch_consistency={_esc(c.get('patch_consistency'))}</span>"
        )

    decisions, warnings = app.split_conflicts(conflicts)

    # -- group 1: genuine disagreements, one ruling each ------------------
    items = []
    for c in decisions:
        prior = adjudicated.get(c["name"])
        done = " done" if prior else ""
        prior_txt = (
            f"<span class='result'>ruled: {_esc(prior['ruling'])} "
            f"by {_esc(prior.get('by'))}</span>"
            if prior
            else "<span class='result'></span>"
        )
        base = {"kind": "conflict", "map_id": map_id, "territory": c["name"]}
        items.append(
            f"<div class='item conflict-item{done}'>"
            f"<div><img src='/crop/territory/{map_id}/{c['territory_id']}.png' "
            f"alt='{_esc(c['name'])}'></div>"
            f"<div class='meta'>{meta_html(c)}</div>"
            "<div class='actions'>"
            f"<button class='yes' onclick='decide(this)' "
            f"data-d=\"{_jattr({**base, 'ruling': 'legend'})}\">legend is right (y)</button>"
            f"<button class='no' onclick='decide(this)' "
            f"data-d=\"{_jattr({**base, 'ruling': 'colour'})}\">colour is right (n)</button>"
            f"<button onclick='decideOther(this)' "
            f"data-d=\"{_jattr({**base, 'ruling': 'other'})}\">neither</button>"
            "<input class='region' size='12' placeholder='region if neither'> "
            "<input class='note' size='16' placeholder='note'>"
            f"{prior_txt}"
            "</div></div>"
        )
    n_done = sum(1 for c in decisions if c["name"] in adjudicated)
    if decisions:
        intro = (
            "<p class='small'><b id='conflict-pos'>"
            f"{n_done} of {len(decisions)} decided</b> &middot; "
            f"{len(decisions)} decision(s) needed — the legend read and the "
            "colour clustering disagree about the region. Likely "
            "colour-method errors first (unreliable or low-consistency "
            "samples are the suspect ones).</p>"
        )
    else:
        intro = (
            "<div class='banner plain'>No decisions needed: every flagged "
            "entry resolves to the same region on both sides (see the "
            "warnings below).</div>"
        )

    # -- group 2: cluster-quality warnings, nothing to rule on ------------
    warn_html = ""
    if warnings:
        warn_items = "".join(
            f"<div class='warnitem'>{meta_html(c)}</div>" for c in warnings
        )
        warn_html = (
            "<details><summary>"
            f"{len(warnings)} cluster-quality warning(s) (legend and colour "
            "agree) — no decision needed</summary>"
            "<p class='small'>These entries are flagged by the merge gate "
            "because their colour cluster is isolated or otherwise "
            "suspect, but the legend and the colour majority resolve to "
            "the SAME region: there is no membership dispute to rule on. "
            "They are listed because they carry a real signal about "
            "sampling quality. The automated merge still refuses on them "
            "— that behaviour is unchanged and correct.</p>"
            f"{warn_items}</details>"
        )
    return intro + "".join(items) + warn_html


def _graph_section(app: ReviewApp, map_id: int, ann: dict) -> str:
    oc = (ann.get("verification") or {}).get("overlay_confirmed") or {}
    if _human_signoff(oc):
        state = (
            f"<div class='banner ok'>overlay confirmed by {_esc(oc.get('by'))} "
            f"at {_esc(oc.get('at'))}</div>"
        )
    elif oc.get("by") and oc.get("verified") is False and oc.get("note"):
        state = (
            f"<div class='banner warn'>problem noted by {_esc(oc.get('by'))}: "
            f"{_esc(oc.get('note'))}</div>"
        )
    else:
        state = ""
    imgs = []
    for name in ("overlay.png", "region_overlay.png"):
        p = app.processed / str(map_id) / name
        if p.is_file():
            imgs.append(
                f"<div class='overlaywrap'><p class='small'>{name}</p>"
                f"<img src='/processed/{map_id}/{name}'></div>"
            )
        else:
            imgs.append(
                f"<p class='small'>{name} has not been built for this map</p>"
            )
    base = {"kind": "overlay", "map_id": map_id}
    return (
        state
        + "".join(imgs)
        + "<div class='item'><div class='meta'>graph + region overlays "
        "above</div><div class='actions'>"
        f"<button class='yes' onclick='decide(this)' "
        f"data-d=\"{_jattr({**base, 'looks_right': True})}\">looks right (y)</button>"
        f"<button class='no' onclick='decide(this)' "
        f"data-d=\"{_jattr({**base, 'looks_right': False})}\">problem (n)</button>"
        "<input class='note' size='30' placeholder='what is wrong?'>"
        "<span class='result'></span>"
        "</div></div>"
    )


def render_map(app: ReviewApp, map_id: int) -> str:
    ann = app.load_ann(map_id)
    summary = app.summary(map_id)
    order = [r["map_id"] for r in app.index_rows()]
    pos = order.index(map_id)
    nxt = order[(pos + 1) % len(order)]
    prv = order[(pos - 1) % len(order)]
    status = app.map_status(map_id)
    crit = " ".join(
        f"<span class='{v}'>{k}={v}</span>"
        for k, v in status["criteria"].items()
    )
    body = (
        f"<p><a href='/'>&larr; index</a> &middot; "
        f"<a href='/map/{prv}'>prev</a> &middot; "
        f"<a id='next-map' href='/map/{nxt}'>next map (Enter)</a> &middot; "
        "<span class='small'>? for keyboard help</span></p>"
        f"<h1>map {map_id} — {_esc(summary.name)}</h1>"
        f"<p>{status['n_territories']} territories &middot; "
        f"{status['n_edges']} edges &middot; {crit}</p>"
        "<h2>1 · Bonuses (the priority)</h2>"
        + _bonus_section(app, map_id, ann)
        + (
            "<h2>2 · Region conflicts — "
            f"{status['n_decisions']} decision(s) needed"
            + (
                f", {status['n_warnings']} warning(s)"
                if status["n_warnings"]
                else ""
            )
            + "</h2>"
        )
        + _conflict_section(app, map_id, ann)
        + "<h2>3 · Graph overlays</h2>"
        + _graph_section(app, map_id, ann)
    )
    return _page(f"map {map_id} review", body, _MAP_JS)


# --------------------------------------------------------------------------
# HTTP plumbing
# --------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    server: "ReviewServer"

    def log_message(self, fmt, *args):  # keep the terminal usable
        pass

    def _send(self, status: int, content_type: str, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _html(self, text: str, status: int = 200) -> None:
        self._send(status, "text/html; charset=utf-8", text.encode())

    def _json(self, doc: dict, status: int = 200) -> None:
        self._send(status, "application/json", json.dumps(doc).encode())

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        app = self.server.app
        parts = [p for p in self.path.split("?")[0].split("/") if p]
        try:
            if not parts:
                return self._html(render_index(app))
            if parts[0] == "map" and len(parts) == 2:
                return self._html(render_map(app, int(parts[1])))
            if parts[0] == "crop" and len(parts) >= 3:
                what = parts[1]
                mid = int(parts[2].removesuffix(".png"))
                if what == "legend":
                    return self._send(200, "image/png", app.crop_legend(mid))
                if what == "bonus" and len(parts) == 4:
                    rid = int(parts[3].removesuffix(".png"))
                    return self._send(200, "image/png", app.crop_bonus(mid, rid))
                if what == "territory" and len(parts) == 4:
                    tid = int(parts[3].removesuffix(".png"))
                    return self._send(
                        200, "image/png", app.crop_territory(mid, tid)
                    )
            if parts[0] == "processed" and len(parts) == 3:
                return self._send(
                    200, "image/png", app.processed_png(int(parts[1]), parts[2])
                )
        except ReviewError as exc:
            return self._json({"error": str(exc)}, exc.status)
        except (ValueError, FileNotFoundError) as exc:
            return self._json({"error": str(exc)}, 404)
        self._json({"error": f"no route for {self.path}"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?")[0] != "/api/decision":
            return self._json({"error": f"no route for {self.path}"}, 404)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            result = self.server.app.record_decision(body)
        except ReviewError as exc:
            return self._json({"error": str(exc)}, exc.status)
        except (json.JSONDecodeError, ValueError) as exc:
            return self._json({"error": f"bad request: {exc}"}, 400)
        self._json(result)


class ReviewServer(ThreadingHTTPServer):
    """Bound to 127.0.0.1 ONLY -- the artwork must never be exposed."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, app: ReviewApp, port: int = 8765):
        self.app = app
        super().__init__(("127.0.0.1", port), _Handler)


def make_server(app: ReviewApp, port: int = 8765) -> ReviewServer:
    return ReviewServer(app, port)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="local review UI for human sign-off (binds 127.0.0.1 only)"
    )
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument(
        "--by",
        default=None,
        help="sign-off identity; prompted for at startup if omitted -- "
        "without one, no sign-off is ever recorded",
    )
    ap.add_argument("--open", action="store_true", help="open a browser")
    args = ap.parse_args(argv)
    reviewer = (args.by or "").strip()
    if not reviewer:
        try:
            reviewer = input(
                "Sign-off identity (e.g. 'Jeremy Manning'; blank runs "
                "read-only): "
            ).strip()
        except EOFError:
            reviewer = ""
    if not reviewer:
        print(
            "NO IDENTITY SET: the UI will display everything but REFUSE to "
            "record any sign-off.",
            file=sys.stderr,
        )
    app = ReviewApp(reviewer=reviewer or None)
    server = make_server(app, args.port)
    host, port = server.server_address[:2]
    url = f"http://{host}:{port}/"
    print(f"review UI at {url} (bound to {host} only; Ctrl-C to stop)")
    if args.open:
        import webbrowser

        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
