import pytest
from riskdyn.config import PermissionRecord, Settings
from riskdyn.sources.d12.fetch import BASE_URL, D12Client, UnexpectedRedirect, _union_robots
from riskdyn.sources.d12.robots import RobotsDisallowed, RobotsPolicy


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


@pytest.mark.network
def test_redirect_raises_and_does_not_poison_cache(tmp_path):
    # /mappanel/map/1 is robots-allowed but genuinely 302s on the live site
    # (verified during reconnaissance). D12Client must never follow it, and
    # must never cache the redirect body under the requested URL. This is the
    # single live call this test needs: the exception carries the real
    # status code and Location header, so we can check both the exception's
    # attributes and that the message embeds them, without a second request.
    client = D12Client(Settings(cache_dir=tmp_path))
    url = f"{BASE_URL}/mappanel/map/1"
    try:
        with pytest.raises(UnexpectedRedirect) as exc:
            client.get("/mappanel/map/1")
    finally:
        client.close()

    assert exc.value.status_code == 302
    assert exc.value.location  # a real Location header was present
    message = str(exc.value)
    assert str(exc.value.status_code) in message
    assert exc.value.location in message
    # Nothing was ever written to the cache for this URL.
    assert client.cache.get(url) is None


def test_refresh_robots_union_never_narrows():
    # Direct, offline test of the merge logic `refresh_robots` relies on: a
    # live robots.txt fetch may only ever ADD disallow rules to what's already
    # in effect, never remove one. Built entirely from strings; no network.
    base = RobotsPolicy.parse(
        "User-agent: *\n"
        "Disallow: /game/\n"
        "Disallow: /user/\n"
        "Disallow: /userlist\n"
    )

    # A "refreshed" policy that omits /game/ (e.g. malformed/truncated
    # response) must not be able to remove that protection.
    narrower = RobotsPolicy.parse("User-agent: *\nDisallow: /user/\n")
    merged = _union_robots(base, narrower)
    assert not merged.is_allowed("/game/112358")
    assert not merged.is_allowed("/userlist")

    # A refreshed policy that adds a genuinely new rule must take effect.
    wider = RobotsPolicy.parse("User-agent: *\nDisallow: /newsection/\n")
    merged2 = _union_robots(merged, wider)
    assert not merged2.is_allowed("/newsection/123")
    # Old rules are still present after the second merge too.
    assert not merged2.is_allowed("/game/112358")
    assert not merged2.is_allowed("/userlist")
