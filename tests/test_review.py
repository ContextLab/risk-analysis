"""The review UI (riskdyn.workbench.review) over the REAL server and data.

Every test drives the real http.server over a real socket on 127.0.0.1
with urllib -- no mocks.  Tests that record decisions operate on copies
under tmp_path; the committed data under data/authored/ is only ever
read.
"""
from __future__ import annotations

import io
import json
import pathlib
import shutil
import threading
import urllib.error
import urllib.request

import pytest

from riskdyn.workbench import review
from riskdyn.workbench.graph_build import (
    _human_signoff,
    bonus_payload_sha256,
    build_graph_map,
)
from riskdyn.workbench.legend_schema import validate_legend_v3

REPO = pathlib.Path(__file__).resolve().parents[1]
AUTHORED = REPO / "data" / "authored" / "maps"
PROCESSED = REPO / "data" / "processed" / "maps"


# --------------------------------------------------------------------------
# plumbing
# --------------------------------------------------------------------------

def _serve(app: review.ReviewApp):
    server = review.make_server(app, 0)  # ephemeral port, still 127.0.0.1
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    return server, base


def _get(base: str, path: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(base + path) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _post(base: str, payload: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        base + "/api/decision",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _copy_map(
    tmp_authored: pathlib.Path,
    tmp_processed: pathlib.Path,
    map_id: int,
    processed_names: tuple[str, ...] = (),
) -> None:
    src = AUTHORED / str(map_id)
    dst = tmp_authored / str(map_id)
    dst.mkdir(parents=True)
    for f in src.iterdir():
        shutil.copy(f, dst / f.name)
    if processed_names:
        pdst = tmp_processed / str(map_id)
        pdst.mkdir(parents=True)
        for name in processed_names:
            shutil.copy(PROCESSED / str(map_id) / name, pdst / name)


@pytest.fixture
def sandbox(tmp_path):
    """Server over COPIES of maps 1 (merged regions + legend), 7 and 100
    (open conflicts + unmerged legends) and 2 (no legend at all)."""
    ta = tmp_path / "authored"
    tp = tmp_path / "processed"
    _copy_map(ta, tp, 1)
    _copy_map(ta, tp, 2)
    _copy_map(ta, tp, 7, ("region_conflicts.json", "region_sample.json"))
    _copy_map(ta, tp, 100, ("region_conflicts.json", "region_sample.json"))
    app = review.ReviewApp(
        reviewer="Test Reviewer", authored_root=ta, processed_root=tp
    )
    server, base = _serve(app)
    yield {"app": app, "base": base, "authored": ta, "processed": tp}
    server.shutdown()
    server.server_close()


@pytest.fixture
def real_readonly():
    """Server over the committed data; used only for GETs."""
    app = review.ReviewApp(reviewer=None)
    server, base = _serve(app)
    yield server, base
    server.shutdown()
    server.server_close()


def _ann(root: pathlib.Path, map_id: int) -> dict:
    return json.loads((root / str(map_id) / "annotations.json").read_text())


def _confirm_all(base: str, root: pathlib.Path, map_id: int) -> None:
    for r in _ann(root, map_id)["regions"]:
        status, res = _post(
            base,
            {"map_id": map_id, "kind": "bonus_confirm",
             "region_id": r["region_id"]},
        )
        assert status == 200, res


# --------------------------------------------------------------------------
# the server itself
# --------------------------------------------------------------------------

def test_server_binds_localhost_only(real_readonly):
    server, _ = real_readonly
    assert server.server_address[0] == "127.0.0.1"
    assert server.server_address[0] != "0.0.0.0"


def test_index_lists_all_maps_with_true_signoff_count(real_readonly):
    _, base = real_readonly
    status, body = _get(base, "/")
    assert status == 200
    html = body.decode()
    map_ids = sorted(
        int(p.name)
        for p in AUTHORED.iterdir()
        if p.name.isdigit() and (p / "annotations.json").is_file()
    )
    assert len(map_ids) == 78
    for mid in map_ids:
        assert f"location='/map/{mid}'" in html
    assert html.count("<tr onclick") == len(map_ids)
    # the true count, computed independently from the authored files
    signed = sum(
        1
        for mid in map_ids
        if _human_signoff(
            (_ann(AUTHORED, mid).get("verification") or {}).get(
                "bonuses_confirmed"
            )
        )
    )
    assert f"bonuses signed off: <b>{signed} of {len(map_ids)}</b>" in html


def test_map_without_legend_says_so_plainly(sandbox):
    status, body = _get(sandbox["base"], "/map/2")
    assert status == 200
    html = body.decode()
    assert "No legend read exists yet for this map" in html
    assert "nothing to confirm" in html.lower()


def test_bonus_crop_is_3x_nearest_of_the_bbox(sandbox):
    from PIL import Image

    ann = _ann(sandbox["authored"], 1)
    region = ann["regions"][0]
    x, y, w, h = region["bonus_bbox"]
    status, body = _get(
        sandbox["base"], f"/crop/bonus/1/{region['region_id']}.png"
    )
    assert status == 200
    assert body[:4] == b"\x89PNG"
    with Image.open(io.BytesIO(body)) as im:
        # 3px context margin on every side, then 3x
        assert im.size == ((w + 6) * 3, (h + 6) * 3)


# --------------------------------------------------------------------------
# bonuses: sign-off, staleness, corrections
# --------------------------------------------------------------------------

def test_bonus_confirmation_signs_off_and_crit_d_passes(sandbox):
    base, ta, tp = sandbox["base"], sandbox["authored"], sandbox["processed"]
    _confirm_all(base, ta, 1)
    ann = _ann(ta, 1)
    bc = ann["verification"]["bonuses_confirmed"]
    assert bc["verified"] is True
    assert bc["by"] == "Test Reviewer"
    assert bc["at"]
    assert bc["payload_sha256"] == bonus_payload_sha256(ann)
    report = build_graph_map(1, out_root=tp, authored_root=ta)
    assert report["criteria"]["d_bonuses_accurate"]["status"] == "pass"
    assert report["criteria"]["d_bonuses_accurate"]["stale_signoff"] is False


def test_changing_a_bonus_value_makes_the_signoff_stale(sandbox):
    base, ta, tp = sandbox["base"], sandbox["authored"], sandbox["processed"]
    _confirm_all(base, ta, 1)
    report = build_graph_map(1, out_root=tp, authored_root=ta)
    assert report["criteria"]["d_bonuses_accurate"]["status"] == "pass"
    # the data changes AFTER the human approved it
    ann_path = ta / "1" / "annotations.json"
    ann = json.loads(ann_path.read_text())
    ann["regions"][0]["bonus"] = ann["regions"][0]["bonus"] + 1
    ann_path.write_text(json.dumps(ann, indent=1))
    report = build_graph_map(1, out_root=tp, authored_root=ta)
    crit_d = report["criteria"]["d_bonuses_accurate"]
    assert crit_d["status"] == "unverified"
    assert crit_d["stale_signoff"] is True
    # and the UI says so, in words
    status, body = _get(base, "/map/1")
    assert status == 200
    assert "Data changed since sign-off" in body.decode()
    status, body = _get(base, "/")
    assert "STALE" in body.decode()


def test_correction_updates_annotations_and_the_v3_legend(sandbox):
    base, ta = sandbox["base"], sandbox["authored"]
    before = _ann(ta, 1)
    region = before["regions"][0]
    rid, was = region["region_id"], region["bonus"]
    corrected = was + 2
    status, res = _post(
        base,
        {"map_id": 1, "kind": "bonus_wrong", "region_id": rid,
         "value": corrected, "note": "misread digit"},
    )
    assert status == 200, res
    ann = _ann(ta, 1)
    fixed = next(r for r in ann["regions"] if r["region_id"] == rid)
    assert fixed["bonus"] == corrected
    assert fixed["bonus_text_verbatim"] == str(corrected)
    corr = ann["verification"]["bonuses_confirmed"]["corrections"]
    assert {
        "region_id": rid, "was": was, "now": corrected,
    }.items() <= corr[-1].items()
    assert corr[-1]["note"] == "misread digit"
    # the legend file carries the correction too, and still validates as v3
    legend = json.loads((ta / "1" / "legend-map1.json").read_text())
    validate_legend_v3(legend)
    lr = next(r for r in legend["regions"] if r["region_id"] == rid)
    assert lr["bonus"]["value"] == corrected
    assert lr["bonus"]["text_verbatim"] == str(corrected)
    # a correction counts as a decision: confirming the rest signs off,
    # and the recorded hash covers the CORRECTED data
    _confirm_all(base, ta, 1)
    ann = _ann(ta, 1)
    bc = ann["verification"]["bonuses_confirmed"]
    assert bc["verified"] is True
    assert bc["payload_sha256"] == bonus_payload_sha256(ann)


def test_unmerged_legend_map_offers_bonus_rows(sandbox):
    # map 100's merge is refused (23 conflicts) so annotations has no
    # regions, but the legend read exists: its values must be reviewable
    status, body = _get(sandbox["base"], "/map/100")
    assert status == 200
    html = body.decode()
    assert "legend read (not yet merged into annotations)" in html
    assert "bonus_confirm" in html


# --------------------------------------------------------------------------
# conflicts
# --------------------------------------------------------------------------

def test_adjudication_is_recorded_for_the_right_territory(sandbox):
    base, ta, tp = sandbox["base"], sandbox["authored"], sandbox["processed"]
    conflicts = json.loads(
        (tp / "100" / "region_conflicts.json").read_text()
    )
    leon = next(c for c in conflicts["conflicts"] if c["name"] == "Leon")
    # precondition: Leon is a genuine disagreement, not a warning
    assert leon["legend_region_id"] != leon["cluster_majority_region_id"]
    status, res = _post(
        base,
        {"map_id": 100, "kind": "conflict", "territory": "Leon",
         "ruling": "legend", "note": "checked the artwork"},
    )
    assert status == 200, res
    entries = _ann(ta, 100)["verification"]["conflicts_adjudicated"]
    assert len(entries) == 1
    e = entries[0]
    assert e["territory"] == "Leon"
    assert e["ruling"] == "legend"
    assert e["region_id"] == leon["legend_region_id"]
    assert e["by"] == "Test Reviewer"
    assert e["at"]
    # re-adjudicating replaces, never duplicates
    status, _ = _post(
        base,
        {"map_id": 100, "kind": "conflict", "territory": "Leon",
         "ruling": "colour"},
    )
    assert status == 200
    entries = _ann(ta, 100)["verification"]["conflicts_adjudicated"]
    assert len(entries) == 1
    assert entries[0]["ruling"] == "colour"
    assert entries[0]["region_id"] == leon["cluster_majority_region_id"]


def test_adjudication_refuses_unknown_territory(sandbox):
    status, res = _post(
        sandbox["base"],
        {"map_id": 100, "kind": "conflict", "territory": "Atlantis",
         "ruling": "legend"},
    )
    assert status == 404
    assert "no open conflict" in res["error"]


def test_conflicts_split_into_decisions_and_warnings(sandbox):
    # measured on the real files: map 100 = 19 genuine disagreements + 4
    # same-region singleton artifacts; map 7 = 4 genuine + 0
    base = sandbox["base"]
    status, body = _get(base, "/map/100")
    assert status == 200
    html = body.decode()
    assert "19 decision(s) needed, 4 warning(s)" in html
    assert "0 of 19 decided" in html
    assert html.count("legend is right (y)") == 19
    assert (
        "4 cluster-quality warning(s) (legend and colour agree) — "
        "no decision needed" in html
    )
    # the four same-region entries are listed as warnings, without buttons
    for name in ("Castile", "Billungermark", "Benevento", "Rome"):
        assert name in html
    warn_block = html.split("<details>")[1].split("</details>")[0]
    assert "legend is right" not in warn_block
    assert "Castile" in warn_block

    status, body = _get(base, "/map/7")
    assert status == 200
    html7 = body.decode()
    assert "4 decision(s) needed" in html7
    assert "warning(s)" not in html7.split("<h2>")[2]  # conflicts heading
    assert html7.count("legend is right (y)") == 4
    assert "cluster-quality warning" not in html7


def test_index_counts_decisions_not_warnings(sandbox):
    status, body = _get(sandbox["base"], "/")
    assert status == 200
    html = body.decode()
    assert "19 open" in html  # map 100: decisions only
    assert "23 open" not in html
    assert "+4 warn" in html  # the warnings stay visible, small


def test_warning_entry_refuses_a_ruling(sandbox):
    ta = sandbox["authored"]
    before = (ta / "100" / "annotations.json").read_bytes()
    status, res = _post(
        sandbox["base"],
        {"map_id": 100, "kind": "conflict", "territory": "Castile",
         "ruling": "legend"},
    )
    assert status == 400
    assert "nothing to rule on" in res["error"]
    assert (ta / "100" / "annotations.json").read_bytes() == before


def test_conflict_position_counts_adjudications(sandbox):
    base = sandbox["base"]
    status, _ = _post(
        base,
        {"map_id": 100, "kind": "conflict", "territory": "Leon",
         "ruling": "colour"},
    )
    assert status == 200
    status, body = _get(base, "/map/100")
    assert status == 200
    assert "1 of 19 decided" in body.decode()


# --------------------------------------------------------------------------
# refusals and write discipline
# --------------------------------------------------------------------------

def test_refuses_to_record_without_identity(sandbox, tmp_path):
    app = review.ReviewApp(
        reviewer=None,
        authored_root=sandbox["authored"],
        processed_root=sandbox["processed"],
    )
    server, base = _serve(app)
    try:
        before = (sandbox["authored"] / "1" / "annotations.json").read_bytes()
        status, res = _post(
            base, {"map_id": 1, "kind": "bonus_confirm", "region_id": 1}
        )
        assert status == 403
        assert "identity" in res["error"]
        after = (sandbox["authored"] / "1" / "annotations.json").read_bytes()
        assert after == before  # nothing was written
    finally:
        server.shutdown()
        server.server_close()


def test_refuses_an_agent_identity(sandbox):
    app = review.ReviewApp(
        reviewer="Claude Agent",
        authored_root=sandbox["authored"],
        processed_root=sandbox["processed"],
    )
    server, base = _serve(app)
    try:
        status, res = _post(
            base, {"map_id": 1, "kind": "bonus_confirm", "region_id": 1}
        )
        assert status == 403
        assert "HUMAN" in res["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_atomic_write_preserves_unrelated_keys(sandbox):
    base, ta = sandbox["base"], sandbox["authored"]
    path = ta / "100" / "annotations.json"
    before = json.loads(path.read_text())
    status, _ = _post(
        base,
        {"map_id": 100, "kind": "conflict", "territory": "Leon",
         "ruling": "legend"},
    )
    assert status == 200
    after = json.loads(path.read_text())
    assert list(after.keys()) == list(before.keys())
    for key in before:
        if key == "verification":
            continue
        assert json.dumps(before[key], indent=1) == json.dumps(
            after[key], indent=1
        ), f"unrelated key {key!r} changed"
    # and the raw file carries the unrelated keys byte-identically: strip
    # the verification block from both serializations and compare
    leftovers = list(path.parent.glob(".*.tmp"))
    assert leftovers == [], f"tempfile left behind: {leftovers}"


def test_overlay_verdict_is_recorded(sandbox):
    base, ta = sandbox["base"], sandbox["authored"]
    status, res = _post(
        base,
        {"map_id": 100, "kind": "overlay", "looks_right": True},
    )
    assert status == 200, res
    oc = _ann(ta, 100)["verification"]["overlay_confirmed"]
    assert oc["verified"] is True
    assert oc["by"] == "Test Reviewer"
    assert oc["at"]
