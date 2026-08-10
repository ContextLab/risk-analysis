import pytest
from riskdyn.sources.d12.robots import RobotsPolicy

REAL_ROBOTS = """User-agent: *
Disallow: /game/
Disallow: /user/
Disallow: /userlist
"""


def test_parses_real_robots_txt():
    p = RobotsPolicy.parse(REAL_ROBOTS)
    assert p.disallowed == ("/game/", "/user/", "/userlist")


@pytest.mark.parametrize("path", ["/maps", "/api/user/names", "/mappanel/map/1",
                                  "/assets/img/maps/1.large.jpg", "/image/map/1.large.circles.jpg"])
def test_allows_permitted_paths(path):
    assert RobotsPolicy.parse(REAL_ROBOTS).is_allowed(path) is True


@pytest.mark.parametrize("path", ["/game/112358", "/game/112358/play/update-state",
                                  "/user/55893", "/userlist", "/userlist?page=2"])
def test_blocks_disallowed_paths(path):
    assert RobotsPolicy.parse(REAL_ROBOTS).is_allowed(path) is False


def test_prefix_match_does_not_overreach():
    # "/userlist" must not block "/users-something-else"
    assert RobotsPolicy.parse(REAL_ROBOTS).is_allowed("/usersettings") is True
