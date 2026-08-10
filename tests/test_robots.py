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


def test_blocks_dot_segment_traversal():
    # /maps/../game/123 resolves to /game/123, which is disallowed
    assert RobotsPolicy.parse(REAL_ROBOTS).is_allowed("/maps/../game/123") is False


def test_blocks_percent_encoded_slash():
    # /game%2F123 decodes to /game/123, which is disallowed
    assert RobotsPolicy.parse(REAL_ROBOTS).is_allowed("/game%2F123") is False


def test_case_sensitivity():
    # robots.txt paths are case-sensitive; /GAME/123 != /game/123
    assert RobotsPolicy.parse(REAL_ROBOTS).is_allowed("/GAME/123") is True


def test_allows_dot_segment_normalization():
    # /maps/./index normalizes to /maps/index, which is allowed
    assert RobotsPolicy.parse(REAL_ROBOTS).is_allowed("/maps/./index") is True


def test_allows_traversal_out_of_disallowed():
    # /userlist/../maps resolves to /maps, which is allowed
    assert RobotsPolicy.parse(REAL_ROBOTS).is_allowed("/userlist/../maps") is True


def test_allows_empty_string():
    # Empty string should be allowed and not raise
    assert RobotsPolicy.parse(REAL_ROBOTS).is_allowed("") is True


def test_allows_bare_slash():
    # Bare "/" should be allowed and not raise
    assert RobotsPolicy.parse(REAL_ROBOTS).is_allowed("/") is True


def test_blocks_double_leading_slash():
    # //game//123 collapses to /game/123 after slash normalization and normpath
    assert RobotsPolicy.parse(REAL_ROBOTS).is_allowed("//game//123") is False


def test_blocks_triple_leading_slash():
    # ///game/123 collapses to /game/123 after slash normalization
    assert RobotsPolicy.parse(REAL_ROBOTS).is_allowed("///game/123") is False


def test_blocks_nested_percent_encoding():
    # /game%252F123 decodes in two passes: %25 -> %, leaving %2F, which decodes to /
    assert RobotsPolicy.parse(REAL_ROBOTS).is_allowed("/game%252F123") is False


def test_blocks_triple_percent_encoding():
    # /game%25252F123 requires multiple decode iterations to resolve
    assert RobotsPolicy.parse(REAL_ROBOTS).is_allowed("/game%25252F123") is False


def test_allows_double_leading_slash_to_allowed():
    # //maps collapses to /maps, which is allowed
    assert RobotsPolicy.parse(REAL_ROBOTS).is_allowed("//maps") is True


def test_allows_interior_slash_normalization():
    # /maps//index normalizes to /maps/index, which is allowed
    assert RobotsPolicy.parse(REAL_ROBOTS).is_allowed("/maps//index") is True
