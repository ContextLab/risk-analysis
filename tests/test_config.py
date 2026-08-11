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


def test_covers_path_traversal_attack(tmp_path):
    """Verify that path traversal in plain form is refused."""
    p = tmp_path / "perm.json"
    p.write_text(json.dumps({
        "granted_by": "D12 admin",
        "granted_on": "2026-08-24",
        "allowed_prefixes": ["/game/"],
        "rate_limit_seconds": 3.0,
    }))
    rec = PermissionRecord.load(p)
    assert rec.covers("/game/../secret") is False


def test_covers_percent_encoded_path_traversal(tmp_path):
    """Verify that percent-encoded path traversal is refused."""
    p = tmp_path / "perm.json"
    p.write_text(json.dumps({
        "granted_by": "D12 admin",
        "granted_on": "2026-08-24",
        "allowed_prefixes": ["/game/"],
        "rate_limit_seconds": 3.0,
    }))
    rec = PermissionRecord.load(p)
    assert rec.covers("/game/%2e%2e/secret") is False


def test_covers_netloc_refused(tmp_path):
    """Verify that paths with network location are refused."""
    p = tmp_path / "perm.json"
    p.write_text(json.dumps({
        "granted_by": "D12 admin",
        "granted_on": "2026-08-24",
        "allowed_prefixes": ["/game/"],
        "rate_limit_seconds": 3.0,
    }))
    rec = PermissionRecord.load(p)
    assert rec.covers("//evil.example/game/1") is False


def test_covers_normal_path_still_allowed(tmp_path):
    """Verify that normal paths within grant are still allowed."""
    p = tmp_path / "perm.json"
    p.write_text(json.dumps({
        "granted_by": "D12 admin",
        "granted_on": "2026-08-24",
        "allowed_prefixes": ["/game/"],
        "rate_limit_seconds": 3.0,
    }))
    rec = PermissionRecord.load(p)
    assert rec.covers("/game/123") is True


def test_covers_empty_string_not_covered(tmp_path):
    """Verify that empty string (no path) is not covered by grants."""
    p = tmp_path / "perm.json"
    p.write_text(json.dumps({
        "granted_by": "D12 admin",
        "granted_on": "2026-08-24",
        "allowed_prefixes": ["/game/"],
        "rate_limit_seconds": 3.0,
    }))
    rec = PermissionRecord.load(p)
    assert rec.covers("") is False


def test_load_rejects_malformed_json(tmp_path):
    """Verify that malformed JSON raises ValueError."""
    p = tmp_path / "perm.json"
    p.write_text("{invalid json")
    with __import__("pytest").raises(ValueError, match="Malformed JSON"):
        PermissionRecord.load(p)


def test_load_rejects_missing_granted_by(tmp_path):
    """Verify that missing granted_by raises ValueError."""
    p = tmp_path / "perm.json"
    p.write_text(json.dumps({
        "granted_on": "2026-08-24",
        "allowed_prefixes": ["/game/"],
    }))
    with __import__("pytest").raises(ValueError, match="granted_by"):
        PermissionRecord.load(p)


def test_load_rejects_missing_granted_on(tmp_path):
    """Verify that missing granted_on raises ValueError."""
    p = tmp_path / "perm.json"
    p.write_text(json.dumps({
        "granted_by": "D12 admin",
        "allowed_prefixes": ["/game/"],
    }))
    with __import__("pytest").raises(ValueError, match="granted_on"):
        PermissionRecord.load(p)


def test_load_rejects_missing_allowed_prefixes(tmp_path):
    """Verify that missing allowed_prefixes raises ValueError."""
    p = tmp_path / "perm.json"
    p.write_text(json.dumps({
        "granted_by": "D12 admin",
        "granted_on": "2026-08-24",
    }))
    with __import__("pytest").raises(ValueError, match="allowed_prefixes"):
        PermissionRecord.load(p)


def test_load_rejects_empty_allowed_prefixes(tmp_path):
    """Verify that empty allowed_prefixes raises ValueError."""
    p = tmp_path / "perm.json"
    p.write_text(json.dumps({
        "granted_by": "D12 admin",
        "granted_on": "2026-08-24",
        "allowed_prefixes": [],
    }))
    with __import__("pytest").raises(ValueError, match="empty"):
        PermissionRecord.load(p)


def test_load_rejects_prefix_without_leading_slash(tmp_path):
    """Verify that prefixes without leading slash raise ValueError."""
    p = tmp_path / "perm.json"
    p.write_text(json.dumps({
        "granted_by": "D12 admin",
        "granted_on": "2026-08-24",
        "allowed_prefixes": ["game/"],
    }))
    with __import__("pytest").raises(ValueError, match="start with '/'"):
        PermissionRecord.load(p)


def test_load_rejects_zero_rate_limit(tmp_path):
    """Verify that zero rate_limit_seconds raises ValueError."""
    p = tmp_path / "perm.json"
    p.write_text(json.dumps({
        "granted_by": "D12 admin",
        "granted_on": "2026-08-24",
        "allowed_prefixes": ["/game/"],
        "rate_limit_seconds": 0,
    }))
    with __import__("pytest").raises(ValueError, match="positive"):
        PermissionRecord.load(p)


def test_load_rejects_negative_rate_limit(tmp_path):
    """Verify that negative rate_limit_seconds raises ValueError."""
    p = tmp_path / "perm.json"
    p.write_text(json.dumps({
        "granted_by": "D12 admin",
        "granted_on": "2026-08-24",
        "allowed_prefixes": ["/game/"],
        "rate_limit_seconds": -1,
    }))
    with __import__("pytest").raises(ValueError, match="positive"):
        PermissionRecord.load(p)


def test_covers_directory_root_matches_its_own_prefix(tmp_path):
    """A "/game/" grant must cover a request for "/game/" itself.

    canonicalize_path strips the trailing slash (posixpath.normpath), so
    without matching both the canonical path and canonical-path-plus-slash,
    "/game/".startswith("/game/") is False and the grant would (harmlessly,
    but inconsistently with the robots gate) fail to cover its own prefix.
    """
    p = tmp_path / "perm.json"
    p.write_text(json.dumps({
        "granted_by": "D12 admin",
        "granted_on": "2026-08-24",
        "allowed_prefixes": ["/game/"],
        "rate_limit_seconds": 3.0,
    }))
    rec = PermissionRecord.load(p)
    assert rec.covers("/game/") is True


def test_load_accepts_blanket_grant(tmp_path):
    """Verify that allowed_prefixes: ['/'] is permitted (blanket grant)."""
    p = tmp_path / "perm.json"
    p.write_text(json.dumps({
        "granted_by": "D12 admin",
        "granted_on": "2026-08-24",
        "allowed_prefixes": ["/"],
    }))
    rec = PermissionRecord.load(p)
    assert rec.covers("/anything") is True
    assert rec.covers("/game/123") is True
    assert rec.covers("/user/xyz") is True
