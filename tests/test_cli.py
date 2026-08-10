import json
import pytest
from riskdyn.cli import main


@pytest.mark.network
def test_pull_catalog_writes_every_map(tmp_path):
    out = tmp_path / "catalog.json"
    assert main(["pull-catalog", "--out", str(out)]) == 0
    catalog = json.loads(out.read_text())
    assert len(catalog) >= 70
    assert any(entry["name"] == "World Classic" for entry in catalog)


def test_unknown_command_returns_nonzero():
    assert main(["nonsense"]) != 0
