import json
from riskdyn.config import PermissionRecord, Settings


def test_no_permission_file_means_no_permission(tmp_path):
    assert PermissionRecord.load(tmp_path / "nope.json") is None


def test_permission_covers_only_listed_prefixes(tmp_path):
    p = tmp_path / "perm.json"
    p.write_text(json.dumps({
        "granted_by": "D12 admin",
        "granted_on": "2026-08-24",
        "allowed_prefixes": ["/game/", "/user/"],
        "rate_limit_seconds": 5.0,
        "notes": "email thread 2026-08-24",
    }))
    rec = PermissionRecord.load(p)
    assert rec.covers("/game/112358") is True
    assert rec.covers("/user/55893") is True
    assert rec.covers("/userlist") is False
    assert rec.rate_limit_seconds == 5.0


def test_settings_defaults_are_conservative():
    s = Settings()
    assert s.rate_limit_seconds >= 3.0
    assert "jeremy.r.manning@dartmouth.edu" in s.user_agent
    assert s.permission is None
