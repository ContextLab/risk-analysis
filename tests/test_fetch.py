import pytest
from riskdyn.config import PermissionRecord, Settings
from riskdyn.sources.d12.fetch import D12Client
from riskdyn.sources.d12.robots import RobotsDisallowed


@pytest.mark.network
def test_fetches_a_robots_allowed_page(tmp_path):
    client = D12Client(Settings(cache_dir=tmp_path))
    try:
        body = client.get("/maps")
    finally:
        client.close()
    assert b"new CreateGame(" in body


@pytest.mark.network
def test_username_api_returns_json_list(tmp_path):
    client = D12Client(Settings(cache_dir=tmp_path))
    try:
        names = client.get_json("/api/user/names?q=setec_astronomy")
    finally:
        client.close()
    assert isinstance(names, list)
    assert "setec_astronomy" in names


@pytest.mark.network
def test_second_get_is_served_from_cache_without_network(tmp_path):
    client = D12Client(Settings(cache_dir=tmp_path))
    try:
        first = client.get("/maps")
    finally:
        client.close()

    # New client, same cache dir, transport closed immediately. A cache miss
    # would now raise instead of silently re-fetching, so passing proves the
    # bytes came from disk.
    offline = D12Client(Settings(cache_dir=tmp_path))
    offline.close()
    assert offline.get("/maps") == first


def test_disallowed_path_is_refused_without_permission(tmp_path):
    client = D12Client(Settings(cache_dir=tmp_path))
    try:
        with pytest.raises(RobotsDisallowed) as exc:
            client.get("/userlist")
        assert "permission" in str(exc.value).lower()
    finally:
        client.close()


def test_permission_record_unlocks_only_its_own_prefixes(tmp_path):
    perm = PermissionRecord(
        granted_by="D12", granted_on="2026-08-24",
        allowed_prefixes=("/game/",), rate_limit_seconds=3.0,
    )
    client = D12Client(Settings(cache_dir=tmp_path, permission=perm))
    try:
        # /userlist is still not covered, so it must still be refused.
        with pytest.raises(RobotsDisallowed):
            client.get("/userlist")
    finally:
        client.close()
