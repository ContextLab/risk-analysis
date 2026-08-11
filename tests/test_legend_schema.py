"""Legend schema v3: validation against the real authored legend files.

Real data only: every test loads the committed legend files under
``data/authored/maps/<id>/legend-map<id>.json`` and, for map 25, D12's own
saved markup.  No mocks, no synthetic fixtures beyond corrupting a real
document to prove the validator rejects the corruption.
"""
import copy
import json

import pytest

from riskdyn.sources.d12.parse_topology import parse_topology
from riskdyn.segment import catalog as cat
from riskdyn.workbench.legend_schema import (
    legend_path,
    load_legend_v3,
    validate_legend_v3,
)

PILOT_MAPS = (1, 7, 100)


def _load(map_id: int) -> dict:
    return load_legend_v3(legend_path(map_id), map_id)


# --------------------------------------------------------------------------
# the real files validate
# --------------------------------------------------------------------------

@pytest.mark.parametrize("map_id", PILOT_MAPS + (25,))
def test_real_legend_file_validates(map_id):
    doc = _load(map_id)
    assert doc["schema_version"] == 3
    assert doc["map_id"] == map_id


def test_schema_version_2_rejected_no_silent_upgrade():
    doc = _load(1)
    doc["schema_version"] = 2
    with pytest.raises(ValueError, match="no silent upgrade"):
        validate_legend_v3(doc)


# --------------------------------------------------------------------------
# gap 3: members are objects, never bare strings
# --------------------------------------------------------------------------

def test_bare_string_member_rejected_with_clear_message():
    doc = copy.deepcopy(_load(1))
    region = doc["regions"][0]
    # regress one real member to the retired v2 territory_names form
    region["members"][0] = region["members"][0]["name"]
    with pytest.raises(ValueError) as exc:
        validate_legend_v3(doc)
    message = str(exc.value)
    assert "bare string" in message
    assert "no silent upgrade" in message
    # the message names the offending member so the author can find it
    assert doc["regions"][0]["members"][0] in message


def test_scalar_name_rejected():
    doc = copy.deepcopy(_load(100))
    doc["regions"][1]["name"] = "Khazars"  # retired v2 scalar form
    with pytest.raises(ValueError, match="must be an object"):
        validate_legend_v3(doc)


def test_bare_string_no_region_entry_rejected():
    doc = copy.deepcopy(_load(100))
    doc["territories_in_no_region"][0] = doc["territories_in_no_region"][0]["name"]
    with pytest.raises(ValueError, match="bare string"):
        validate_legend_v3(doc)


# --------------------------------------------------------------------------
# gap 2: name provenance
# --------------------------------------------------------------------------

def test_inferred_provenance_roundtrips_and_is_distinct_from_printed(tmp_path):
    doc7 = _load(7)
    doc100 = _load(100)
    inferred = {r["region_id"]: r["name"] for r in doc7["regions"]
                if r["name"]["provenance"] == "inferred"}
    # map 7's six city-bonus group names are inferred from the numbered
    # territory-name stems, never printed
    assert sorted(inferred) == [7, 8, 9, 10, 11, 12]
    assert inferred[7]["text"] == "Tigard"
    printed = {r["region_id"] for r in doc100["regions"]
               if r["name"]["provenance"] == "printed"}
    assert 2 in printed  # "Khazars" is transcribed verbatim
    # round-trip through disk keeps the distinction auditable
    p = tmp_path / "roundtrip.json"
    p.write_text(json.dumps(doc7))
    again = json.loads(p.read_text())
    validate_legend_v3(again)
    assert [r["name"]["provenance"] for r in again["regions"]] == [
        r["name"]["provenance"] for r in doc7["regions"]
    ]
    assert inferred[7]["provenance"] != "printed"


def test_none_printed_requires_null_text():
    doc = copy.deepcopy(_load(1))
    doc["regions"][0]["name"]["text"] = "North America"
    with pytest.raises(ValueError, match="none-printed"):
        validate_legend_v3(doc)


def test_printed_unbindable_carries_spanned_regions():
    doc = _load(100)
    by_id = {r["region_id"]: r for r in doc["regions"]}
    # "Regnum Francorum" is one printed label centred over four numerals
    for rid in (6, 7, 8, 9):
        name = by_id[rid]["name"]
        assert name["provenance"] == "printed-unbindable"
        assert name["text"] == "Regnum Francorum"
        assert name["spans_region_ids"] == [6, 7, 8, 9]
    # "Byzantine" sits 2 px from the midpoint of two numerals
    for rid in (13, 14):
        assert by_id[rid]["name"]["provenance"] == "printed-unbindable"
        assert by_id[rid]["name"]["spans_region_ids"] == [13, 14]
    # spans on a plain printed name is an error
    bad = copy.deepcopy(doc)
    bad_by_id = {r["region_id"]: r for r in bad["regions"]}
    bad_by_id[2]["name"]["spans_region_ids"] = [2, 3]
    with pytest.raises(ValueError, match="printed-unbindable"):
        validate_legend_v3(bad)


# --------------------------------------------------------------------------
# map 7: overlay layer
# --------------------------------------------------------------------------

def test_map7_overlay_layer_44_memberships_across_35_territories():
    doc = _load(7)
    layers = {r["region_id"]: r["layer"] for r in doc["regions"]}
    assert [layers[i] for i in range(1, 7)] == ["base"] * 6
    assert [layers[i] for i in range(7, 13)] == ["overlay"] * 6
    memberships = [
        (m["name"], r["layer"]) for r in doc["regions"] for m in r["members"]
    ]
    assert len(memberships) == 44
    member_names = {n for n, _ in memberships}
    no_region = {t["name"] for t in doc["territories_in_no_region"]}
    assert len(member_names | no_region) == 35
    assert len(no_region) == 3
    # exactly the 12 city-pair territories sit in both layers
    dual = {n for n, layer in memberships if layer == "overlay"}
    assert len(dual) == 12
    assert dual <= {n for n, layer in memberships if layer == "base"}
    validate_legend_v3(doc)  # dual membership across layers is legal...

    # ...but two BASE regions claiming one territory is the corruption the
    # layer marker exists to expose
    bad = copy.deepcopy(doc)
    for r in bad["regions"]:
        if r["region_id"] == 7:
            r["layer"] = "base"
    with pytest.raises(ValueError, match="two base-layer regions"):
        validate_legend_v3(bad)


# --------------------------------------------------------------------------
# map 25: map_specific reproduces the directed graph
# --------------------------------------------------------------------------

def test_map25_unit_classes_reproduce_all_146_directed_edges():
    doc = _load(25)
    classes = doc["map_specific"]["unit_classes"]
    # recorded as class_0/1/2, never bound to unit names (that binding
    # needs the painted icons; only a partial spot-check exists)
    assert sorted(classes) == ["class_0", "class_1", "class_2"]
    assert sorted(len(v) for v in classes.values()) == [18, 18, 19]
    gap = [u for u in doc["unresolved"]
           if "which unit" in u["what"]]
    assert gap, "the class->unit binding gap must be recorded in unresolved"

    html = (cat.REPO_ROOT / "data" / "raw" / "saved_pages" / "25.html").read_text()
    topo = parse_topology(html, 25)
    cls = {}
    for k, names in classes.items():
        for n in names:
            cls[n] = int(k.split("_")[1])
    by_id = {t.territory_id: t.name for t in topo.territories}
    assert sorted(cls) == sorted(by_id.values())

    directed = {
        (t.territory_id, a) for t in topo.territories for a in t.adjacencies
    }
    assert len(directed) == 146
    one_way = {(a, b) for (a, b) in directed if (b, a) not in directed}
    assert len(one_way) == 102
    violations = []
    for a, b in directed:
        ca, cb = cls[by_id[a]], cls[by_id[b]]
        if (b, a) in directed:
            ok = ca == cb  # bidirectional => same class
        else:
            ok = cb == (ca + 1) % 3  # one-way A->B => class(B)=class(A)+1
        if not ok:
            violations.append((by_id[a], by_id[b]))
    assert violations == [], f"{len(violations)} of 146 edges violate the cycle"


def test_map_specific_keys_must_be_documented_in_prose():
    doc = copy.deepcopy(_load(25))
    doc["map_specific"]["mystery_key"] = {"x": 1}
    with pytest.raises(ValueError, match="mystery_key"):
        validate_legend_v3(doc)
    # documentation for a key that does not exist is equally an error
    doc2 = copy.deepcopy(_load(25))
    doc2["map_specific"]["documentation"]["ghost"] = "documents nothing"
    with pytest.raises(ValueError, match="ghost"):
        validate_legend_v3(doc2)


def test_map_specific_is_never_required():
    doc = copy.deepcopy(_load(1))
    assert doc["map_specific"] == {}
    validate_legend_v3(doc)
    del doc["map_specific"]
    validate_legend_v3(doc)  # absent is as legal as empty


# --------------------------------------------------------------------------
# map 100: the four red-team corrections are in the authored file
# --------------------------------------------------------------------------

def test_map100_redteam_corrections_applied():
    doc = _load(100)
    member_region = {
        m["name"]: r["region_id"] for r in doc["regions"] for m in r["members"]
    }
    assert member_region["Chernigov"] == 2   # was Rus (r3)
    assert member_region["Kerch"] == 4       # was Khazars (r2)
    assert member_region["Edessa"] == 16     # was Byzantine (r14); bonus 4->3
    assert member_region["Alamania"] == 8    # was r7; bonus 2->3
    assert len(member_region) == 141
    assert len(doc["territories_in_no_region"]) == 9
    # confidence is split, not one scalar: region 14's numeral is certain
    # even though its name binding is low
    r14 = next(r for r in doc["regions"] if r["region_id"] == 14)
    assert r14["bonus"]["confidence"] == "high"
    assert r14["name"]["confidence"] == "low"
    # the label-spanning question is recorded, not resolved
    spans = [u for u in doc["unresolved"] if "Byzantine" in u["what"]]
    assert spans and spans[0]["options"]
    assert any("Regnum Francorum" in u["what"] for u in doc["unresolved"])
