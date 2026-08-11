"""merge_legend: the colour cross-check gate, on real map data.

Every test stages COPIES of the real committed files (annotations.json,
legend-map<id>.json, region_sample.json) into a tmp tree so the repo data
is never mutated, then runs the real merge against them.  No mocks.
"""
import json
import pathlib
import shutil

import pytest

from riskdyn.workbench.build import AUTHORED_ROOT, PROCESSED_ROOT
from riskdyn.workbench.merge_legend import (
    MergeRefused,
    main,
    merge_legend_map,
)

# the 23 unadjudicated map-100 disagreements between the corrected legend
# read and the independent colour clustering (see legend-redteam.md):
# 6 Umayyad members sharing region 22's exact fill, 6+6 members of the two
# evenly-split clusters, Leon (clustered with Hamdanid), and 4 territories
# whose colour isolates them from every claimed region-mate
MAP100_EXPECTED_CONFLICTS = {
    "Aquitaine", "Ardebil", "Audjila", "Barqah", "Benevento",
    "Billungermark", "Bretagne", "Castile", "Cordoba", "Derbent", "Dvin",
    "Farigha", "Flanders", "Gasgony", "Leon", "Lisbon", "Malaga",
    "Manzikert", "Marmarica", "Neustria", "Paris", "Qasr", "Rome",
    "Santariyya", "Seville", "Shirvan", "Tiflis", "Toledo", "Zaragoza",
}
# exactly one side of each evenly-split cluster is flagged (the tie-break
# is deterministic and marked ambiguous), so 23 of the 29 candidates above
# appear; the four corrected pilot errors appear in NEITHER set
MAP100_CORRECTED = {"Chernigov", "Kerch", "Edessa", "Alamania"}


def _stage(tmp_path: pathlib.Path, map_id: int):
    """Copy the real files for one map into a tmp authored/processed tree."""
    authored = tmp_path / "authored"
    processed = tmp_path / "processed"
    (authored / str(map_id)).mkdir(parents=True, exist_ok=True)
    (processed / str(map_id)).mkdir(parents=True, exist_ok=True)
    for name in ("annotations.json", f"legend-map{map_id}.json"):
        src = AUTHORED_ROOT / str(map_id) / name
        if src.is_file():
            shutil.copy(src, authored / str(map_id) / name)
    shutil.copy(
        PROCESSED_ROOT / str(map_id) / "region_sample.json",
        processed / str(map_id) / "region_sample.json",
    )
    return authored, processed


def _read(root: pathlib.Path, map_id: int, name: str) -> dict:
    return json.loads((root / str(map_id) / name).read_text())


# --------------------------------------------------------------------------
# map 100: the gate refuses
# --------------------------------------------------------------------------

def test_map100_refuses_and_writes_exactly_23_conflicts(tmp_path):
    authored, processed = _stage(tmp_path, 100)
    before = _read(authored, 100, "annotations.json")
    with pytest.raises(MergeRefused, match="23 of 141"):
        merge_legend_map(100, authored_root=authored, out_root=processed)
    doc = _read(processed, 100, "region_conflicts.json")
    assert doc["n_conflicts"] == 23
    assert len(doc["conflicts"]) == 23
    names = {c["name"] for c in doc["conflicts"]}
    assert len(names) == 23
    assert names <= MAP100_EXPECTED_CONFLICTS
    # the four corrected pilot errors now AGREE with their clusters; the
    # remaining 23 are unadjudicated and refusing is the correct outcome
    assert not names & MAP100_CORRECTED
    # a refusal writes nothing into the authored annotations
    assert _read(authored, 100, "annotations.json") == before
    for c in doc["conflicts"]:
        assert c["reason"]
        assert isinstance(c["ambiguous_majority"], bool)


def test_map100_agrees_with_cluster_is_never_trusted_from_input(tmp_path):
    authored, processed = _stage(tmp_path, 100)
    leg_path = authored / "100" / "legend-map100.json"
    doc = json.loads(leg_path.read_text())
    for r in doc["regions"]:
        for m in r["members"]:
            m["agrees_with_cluster"] = True  # lie in the input
            m["colour_cluster"] = 999
    leg_path.write_text(json.dumps(doc))
    with pytest.raises(MergeRefused, match="23 of 141"):
        merge_legend_map(100, authored_root=authored, out_root=processed)


def test_map100_force_demotes_conflicting_members_and_records_unresolved(tmp_path):
    authored, processed = _stage(tmp_path, 100)
    summary = merge_legend_map(
        100, force=True, authored_root=authored, out_root=processed
    )
    assert summary["n_conflicts"] == 23 and summary["forced"]
    legend = _read(authored, 100, "legend-map100.json")
    conflict_names = {
        c["name"]
        for c in _read(processed, 100, "region_conflicts.json")["conflicts"]
    }
    flagged = set()
    for r in legend["regions"]:
        for m in r["members"]:
            if m["name"] in conflict_names:
                assert m["confidence"] == "low", m["name"]
                assert m["agrees_with_cluster"] is False
                flagged.add(m["name"])
            else:
                assert m["agrees_with_cluster"] is True
                assert isinstance(m["colour_cluster"], int)
    assert flagged == conflict_names
    recorded = {
        u["what"] for u in legend["unresolved"]
        if u["what"].startswith("colour cross-check conflict")
    }
    assert len(recorded) == 23
    for name in conflict_names:
        assert any(name in w for w in recorded)
    # the merged annotations carry the demoted confidence per territory
    ann = _read(authored, 100, "annotations.json")
    by_name = {t["name"]: t for t in ann["territories"]}
    for name in conflict_names:
        assert by_name[name]["region_confidence"] == "low"
    assert len(ann["regions"]) == 24
    assert all(r["source"] == "legend-read" for r in ann["regions"])
    # membership follows the corrected legend, including the four fixes
    assert by_name["Chernigov"]["region_ids"] == [2]
    assert by_name["Kerch"]["region_ids"] == [4]
    assert by_name["Edessa"]["region_ids"] == [16]
    assert by_name["Alamania"]["region_ids"] == [8]
    assert sum(len(t["region_ids"]) for t in ann["territories"]) == 141


# --------------------------------------------------------------------------
# map 7: overlay membership -> two region_ids entries
# --------------------------------------------------------------------------

def test_map7_dual_layer_membership_yields_two_region_ids(tmp_path):
    authored, processed = _stage(tmp_path, 7)
    # map 7 has 4 genuine method disagreements (the parchment-sampling
    # artefacts the red-team documented), so a plain merge refuses...
    with pytest.raises(MergeRefused, match="4 of 32"):
        merge_legend_map(7, authored_root=authored, out_root=processed)
    conflicts = _read(processed, 7, "region_conflicts.json")
    assert {c["name"] for c in conflicts["conflicts"]} == {
        "Metzger", "Butteville", "Donald", "Aurora"
    }
    # ...and --force proceeds
    merge_legend_map(7, force=True, authored_root=authored, out_root=processed)
    ann = _read(authored, 7, "annotations.json")
    by_name = {t["name"]: t for t in ann["territories"]}
    # a territory in a base colour region AND an overlay bonus group gets
    # TWO entries in region_ids
    assert by_name["Tigard 1"]["region_ids"] == [1, 7]
    assert by_name["Tigard 2"]["region_ids"] == [1, 7]
    assert by_name["Tualatin 1"]["region_ids"] == [1, 8]
    assert by_name["Oregon City 2"]["region_ids"] == [3, 11]
    assert by_name["Newberg 1"]["region_ids"] == [4, 12]
    dual = [t for t in ann["territories"] if len(t["region_ids"]) == 2]
    assert len(dual) == 12
    # grey territories stay in no region
    for name in ("Ladd Hill", "Saint Paul", "Mulino"):
        assert by_name[name]["region_ids"] == []
    assert sum(len(t["region_ids"]) for t in ann["territories"]) == 44
    # overlay members never get a fabricated colour verdict
    legend = _read(authored, 7, "legend-map7.json")
    for r in legend["regions"]:
        for m in r["members"]:
            if r["layer"] == "overlay":
                assert m["agrees_with_cluster"] is None
            else:
                assert isinstance(m["agrees_with_cluster"], bool)


# --------------------------------------------------------------------------
# map 1: clean merge; no map_specific key is ever assumed
# --------------------------------------------------------------------------

def test_map1_merges_cleanly_without_any_map_specific_keys(tmp_path):
    authored, processed = _stage(tmp_path, 1)
    # strip map_specific entirely: the merge must not assume it exists
    leg_path = authored / "1" / "legend-map1.json"
    doc = json.loads(leg_path.read_text())
    doc.pop("map_specific", None)
    leg_path.write_text(json.dumps(doc))
    summary = merge_legend_map(1, authored_root=authored, out_root=processed)
    assert summary["n_conflicts"] == 0
    assert _read(processed, 1, "region_conflicts.json")["n_conflicts"] == 0
    ann = _read(authored, 1, "annotations.json")
    assert len(ann["regions"]) == 6
    assert all(len(t["region_ids"]) == 1 for t in ann["territories"])
    # the artwork prints no region names anywhere on map 1; the provenance
    # split keeps that auditable in the merged annotations
    assert all(r["name"] is None for r in ann["regions"])
    assert all(r["name_provenance"] == "none-printed" for r in ann["regions"])


# --------------------------------------------------------------------------
# out of scope: no legend, or a legend with no regions
# --------------------------------------------------------------------------

def test_missing_legend_file_is_out_of_scope_with_clear_message(tmp_path):
    authored, processed = _stage(tmp_path, 1)
    (authored / "1" / "legend-map1.json").unlink()
    with pytest.raises(FileNotFoundError, match="out of scope"):
        merge_legend_map(1, authored_root=authored, out_root=processed)


def test_map25_legend_without_regions_is_out_of_scope(tmp_path):
    # map 25's legend file exists (its map_specific unit classes are
    # derivable mechanically) but no legend regions have been authored, so
    # its clusters cannot be related to legend region ids
    authored, processed = _stage(tmp_path, 25)
    with pytest.raises(MergeRefused, match="out of scope"):
        merge_legend_map(25, authored_root=authored, out_root=processed)


# --------------------------------------------------------------------------
# human sign-off is inviolable
# --------------------------------------------------------------------------

def _sign_off_territory(authored, map_id, name, region_ids, note):
    p = authored / str(map_id) / "annotations.json"
    ann = json.loads(p.read_text())
    t = next(t for t in ann["territories"] if t["name"] == name)
    t["region_ids"] = region_ids
    t["note"] = note
    p.write_text(json.dumps(ann))


def test_human_signed_member_is_never_overwritten_even_with_force(tmp_path):
    authored, processed = _stage(tmp_path, 1)
    # a (hypothetical) human moved Alaska out of region 1 and signed off;
    # the legend disagrees, so the merge must refuse -- force included
    _sign_off_territory(
        authored, 1, "Alaska", [2],
        "membership human-verified against the artwork, 2026-08-10",
    )
    before = _read(authored, 1, "annotations.json")
    for force in (False, True):
        with pytest.raises(MergeRefused, match="Alaska"):
            merge_legend_map(
                1, force=force, authored_root=authored, out_root=processed
            )
        assert _read(authored, 1, "annotations.json") == before


def test_human_signed_member_that_agrees_does_not_block_the_merge(tmp_path):
    authored, processed = _stage(tmp_path, 1)
    # sign-off on a membership the legend agrees with: nothing changes for
    # that member, so the merge proceeds and keeps it intact
    _sign_off_territory(
        authored, 1, "Alaska", [1],
        "membership human-verified against the artwork, 2026-08-10",
    )
    merge_legend_map(1, authored_root=authored, out_root=processed)
    ann = _read(authored, 1, "annotations.json")
    alaska = next(t for t in ann["territories"] if t["name"] == "Alaska")
    assert alaska["region_ids"] == [1]


def test_verification_block_signoff_protects_all_members(tmp_path):
    authored, processed = _stage(tmp_path, 1)
    p = authored / "1" / "annotations.json"
    ann = json.loads(p.read_text())
    ann["verification"]["regions_confirmed"] = {
        "verified": True, "by": "Jeremy Manning", "at": "2026-08-10",
    }
    # make the stored membership differ from the legend for one territory
    t = next(t for t in ann["territories"] if t["name"] == "Ukraine")
    t["region_ids"] = []
    p.write_text(json.dumps(ann))
    with pytest.raises(MergeRefused, match="Ukraine"):
        merge_legend_map(1, force=True, authored_root=authored, out_root=processed)


def test_agent_signoff_never_counts_as_human(tmp_path):
    authored, processed = _stage(tmp_path, 1)
    p = authored / "1" / "annotations.json"
    ann = json.loads(p.read_text())
    ann["verification"]["regions_confirmed"] = {
        "verified": True, "by": "claude-agent (Fable 5)", "at": "2026-08-10",
    }
    t = next(t for t in ann["territories"] if t["name"] == "Ukraine")
    t["region_ids"] = []
    p.write_text(json.dumps(ann))
    merge_legend_map(1, authored_root=authored, out_root=processed)
    ann = _read(authored, 1, "annotations.json")
    ukraine = next(t for t in ann["territories"] if t["name"] == "Ukraine")
    assert ukraine["region_ids"] == [3]  # the legend read wins over an agent


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def test_cli_refusal_exit_code_on_real_map100(capsys):
    # a refusal never mutates the authored files, so running the real CLI
    # path against the repo's map 100 is side-effect-free there (it only
    # rewrites the processed region_conflicts.json artifact)
    before = (AUTHORED_ROOT / "100" / "annotations.json").read_bytes()
    assert main(["100"]) == 2
    assert (AUTHORED_ROOT / "100" / "annotations.json").read_bytes() == before
    err = capsys.readouterr().err
    assert "REFUSED" in err and "23" in err


def test_cli_missing_legend_exit_code(capsys):
    # map 10 has annotations but no authored legend
    assert not (AUTHORED_ROOT / "10" / "legend-map10.json").exists()
    assert main(["10"]) == 2
    assert "out of scope" in capsys.readouterr().err
