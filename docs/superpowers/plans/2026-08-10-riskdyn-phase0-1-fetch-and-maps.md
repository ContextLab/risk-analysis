# riskdyn Phases 0–1: fetch infrastructure and maps

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `riskdyn` package skeleton, a permission-gated and rate-limited D12 fetch layer with on-disk caching, and the maps module that turns D12 map definitions into adjacency graphs and vector renderings.

**Architecture:** Network access is confined to `riskdyn/sources/d12/fetch.py`; every other module operates on cached bytes or parsed objects, so all downstream tests run offline. Parsers are pure functions tested against checked-in fixtures captured from real responses. The fetch layer refuses `robots.txt`-disallowed paths unless an explicit permission record is configured.

**Tech Stack:** Python ≥3.10, `httpx`, `networkx`, `matplotlib`, `pydantic`, `pytest`, `platformdirs`.

## Global Constraints

- **Python ≥3.10.** The repo `Dockerfile` currently pins `contextlab/cdl-python:3.7` with `brainiak`; that is unmodified template leftover, is EOL, and cannot run modern dependencies. Task 1 updates it.
- **No mocks, no mock fallbacks.** Per the repo testing policy, tests use real HTTP calls to robots-allowed endpoints, or real fixtures captured from real responses. If real functionality cannot be verified, the test fails — it does not fall back.
- **Rate limit default 1 request / 3 seconds**, configurable. Never remove it in tests.
- **User-Agent must identify the project and a contact address**, exact value: `riskdyn/0.1 (academic research; jeremy.r.manning@dartmouth.edu)`
- **robots.txt disallows exactly `/game/`, `/user/`, `/userlist`.** `/api/`, `/maps`, `/mappanel/`, `/assets/`, `/image/` are allowed.
- **Never commit real usernames, chat text, or credentials.** `config/`, `data/raw/*`, `data/processed/*`, `.env`, `*.session` are gitignored.
- Package name is `riskdyn`. Repo root is `/Users/jmanning/risk-analysis`.

---

## Reconnaissance already completed

Do not re-derive these; they are verified facts.

| fact | value |
|-|-|
| map catalog | inline JSON on `/maps`, in `new CreateGame({...}, true)` |
| catalog size | 77 maps, 1,232,133 total games |
| catalog fields | `map_id, name, width, height, status, locked, num_territories, num_regions, num_games_recent, num_games_total, caps, author, imageUrl, imageThumbnailUrl, recommended_min_players, recommended_max_players, size, created_at, updated_at` |
| territory count range | 24–150 |
| map image | `/assets/img/maps/<id>.large.jpg`, `.thumbnail.jpg` |
| map image w/ territory circles | `/image/map/<id>.large.circles.jpg` |
| topology location | `#territory-<N>` elements carry `data-adjacencies` (comma-separated ids), `data-territory-id`, `data-region-id`, `data-x`, `data-y` |
| topology access | `/mappanel/map/<id>` → HTTP 302 (login required); `/game/<id>` → robots-disallowed |
| username API | `/api/user/names?q=<prefix>` → JSON array of **usernames only, no ids** |

## File structure

```
pyproject.toml                          package metadata, deps, pytest config
Dockerfile                              MODIFIED: 3.7+brainiak -> 3.11
riskdyn/__init__.py                     version, public re-exports
riskdyn/config.py                       Settings, PermissionRecord
riskdyn/sources/d12/robots.py           robots.txt parse + path gate
riskdyn/sources/d12/ratelimit.py        token-bucket limiter
riskdyn/sources/d12/cache.py            content-addressed on-disk cache
riskdyn/sources/d12/fetch.py            D12Client — the ONLY network code
riskdyn/sources/d12/parse_catalog.py    /maps HTML -> list[MapSummary]
riskdyn/sources/d12/parse_topology.py   map panel HTML -> MapTopology
riskdyn/maps/model.py                   Territory, Region, MapSummary, MapTopology, GameMap
riskdyn/maps/graph.py                   GameMap -> networkx.Graph + invariant checks
riskdyn/maps/render.py                  vector rendering to PDF/SVG
riskdyn/cli.py                          `riskdyn pull-catalog`, `pull-images`
tests/conftest.py                       fixture paths, network marker
tests/fixtures/maps_page.html           real /maps response (captured)
tests/fixtures/mappanel_map1.html       real map panel response (captured, Task 8)
tests/test_robots.py
tests/test_ratelimit.py
tests/test_cache.py
tests/test_fetch.py                     marked `network`
tests/test_parse_catalog.py
tests/test_maps_graph.py
tests/test_parse_topology.py
tests/test_render.py
```

---

### Task 1: Package scaffold, dependencies, and environment

**Files:**
- Create: `pyproject.toml`, `riskdyn/__init__.py`, `riskdyn/sources/__init__.py`, `riskdyn/sources/d12/__init__.py`, `riskdyn/maps/__init__.py`, `tests/conftest.py`
- Modify: `Dockerfile`
- Test: `tests/test_package.py`

**Interfaces:**
- Consumes: nothing
- Produces: importable `riskdyn` package with `riskdyn.__version__: str`; pytest marker `network`; fixture `fixtures_dir() -> pathlib.Path`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_package.py
def test_package_imports_and_has_version():
    import riskdyn
    assert isinstance(riskdyn.__version__, str)
    assert riskdyn.__version__.count(".") >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_package.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'riskdyn'`

- [ ] **Step 3: Create the package and config**

```toml
# pyproject.toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "riskdyn"
version = "0.1.0"
description = "Conversation and position dynamics in online Risk"
requires-python = ">=3.10"
dependencies = [
    "httpx>=0.27",
    "networkx>=3.2",
    "matplotlib>=3.8",
    "pydantic>=2.6",
    "platformdirs>=4.2",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-cov>=5.0"]

[project.scripts]
riskdyn = "riskdyn.cli:main"

[tool.setuptools.packages.find]
include = ["riskdyn*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "network: test performs a real HTTP request to dominating12.com",
]
```

```python
# riskdyn/__init__.py
"""Conversation and position dynamics in online Risk."""

__version__ = "0.1.0"
```

Create empty `riskdyn/sources/__init__.py`, `riskdyn/sources/d12/__init__.py`, `riskdyn/maps/__init__.py`.

```python
# tests/conftest.py
import pathlib
import pytest


@pytest.fixture
def fixtures_dir() -> pathlib.Path:
    return pathlib.Path(__file__).parent / "fixtures"
```

- [ ] **Step 4: Update the Dockerfile**

The current file pins Python 3.7 and `brainiak`, both template leftovers unrelated to this project.

```dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /mnt

RUN pip install --no-cache-dir \
        jupyter notebook \
        httpx networkx matplotlib pydantic platformdirs pytest

ENTRYPOINT ["/usr/bin/env"]
CMD ["bash"]
```

- [ ] **Step 5: Install and run the test**

Run: `pip install -e ".[dev]" && pytest tests/test_package.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml riskdyn tests Dockerfile
git commit -m "feat: scaffold riskdyn package; update Dockerfile off EOL Python 3.7"
```

---

### Task 2: robots.txt gate

**Files:**
- Create: `riskdyn/sources/d12/robots.py`
- Test: `tests/test_robots.py`

**Interfaces:**
- Consumes: nothing
- Produces: `RobotsPolicy(disallowed: tuple[str, ...])`, classmethod `RobotsPolicy.parse(text: str) -> RobotsPolicy`, method `.is_allowed(path: str) -> bool`; exception `RobotsDisallowed(Exception)`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_robots.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_robots.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'riskdyn.sources.d12.robots'`

- [ ] **Step 3: Implement**

```python
# riskdyn/sources/d12/robots.py
"""robots.txt parsing and path gating for dominating12.com.

Only the ``User-agent: *`` group is honored, which is the only group D12
publishes. Directives are prefix matches, per the robots.txt convention.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


class RobotsDisallowed(Exception):
    """Raised when a fetch targets a path robots.txt disallows."""


@dataclass(frozen=True)
class RobotsPolicy:
    disallowed: tuple[str, ...]

    @classmethod
    def parse(cls, text: str) -> "RobotsPolicy":
        rules: list[str] = []
        in_star_group = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            field, value = (part.strip() for part in line.split(":", 1))
            field = field.lower()
            if field == "user-agent":
                in_star_group = value == "*"
            elif field == "disallow" and in_star_group and value:
                rules.append(value)
        return cls(tuple(rules))

    def is_allowed(self, path: str) -> bool:
        """True if ``path`` (a path, or a full URL) may be fetched."""
        candidate = urlsplit(path).path or "/"
        return not any(candidate.startswith(rule) for rule in self.disallowed)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_robots.py -v`
Expected: PASS, 12 tests

- [ ] **Step 5: Commit**

```bash
git add riskdyn/sources/d12/robots.py tests/test_robots.py
git commit -m "feat: robots.txt policy gate for d12 paths"
```

---

### Task 3: Permission record

**Files:**
- Create: `riskdyn/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing
- Produces: `PermissionRecord(granted_by, granted_on, allowed_prefixes, rate_limit_seconds, notes)` with `.covers(path: str) -> bool`, `.load(path) -> PermissionRecord | None`; `Settings(user_agent, cache_dir, rate_limit_seconds, permission)`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'riskdyn.config'`

- [ ] **Step 3: Implement**

```python
# riskdyn/config.py
"""Runtime settings and the data-use permission record.

The permission record is the *only* thing that unlocks robots-disallowed paths.
It exists as an explicit, reviewable artifact so that what was granted is
recorded rather than remembered.
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field

from platformdirs import user_cache_dir

USER_AGENT = "riskdyn/0.1 (academic research; jeremy.r.manning@dartmouth.edu)"
DEFAULT_RATE_LIMIT_SECONDS = 3.0


@dataclass(frozen=True)
class PermissionRecord:
    """A written data-use grant from the site operator."""

    granted_by: str
    granted_on: str
    allowed_prefixes: tuple[str, ...]
    rate_limit_seconds: float = DEFAULT_RATE_LIMIT_SECONDS
    notes: str = ""

    @classmethod
    def load(cls, path: str | pathlib.Path) -> "PermissionRecord | None":
        path = pathlib.Path(path)
        if not path.exists():
            return None
        raw = json.loads(path.read_text())
        return cls(
            granted_by=raw["granted_by"],
            granted_on=raw["granted_on"],
            allowed_prefixes=tuple(raw["allowed_prefixes"]),
            rate_limit_seconds=float(raw.get("rate_limit_seconds", DEFAULT_RATE_LIMIT_SECONDS)),
            notes=raw.get("notes", ""),
        )

    def covers(self, path: str) -> bool:
        return any(path.startswith(prefix) for prefix in self.allowed_prefixes)


@dataclass
class Settings:
    user_agent: str = USER_AGENT
    cache_dir: pathlib.Path = field(
        default_factory=lambda: pathlib.Path(user_cache_dir("riskdyn"))
    )
    rate_limit_seconds: float = DEFAULT_RATE_LIMIT_SECONDS
    permission: PermissionRecord | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS, 3 tests

- [ ] **Step 5: Commit**

```bash
git add riskdyn/config.py tests/test_config.py
git commit -m "feat: permission record gating robots-disallowed paths"
```

---

### Task 4: Rate limiter

**Files:**
- Create: `riskdyn/sources/d12/ratelimit.py`
- Test: `tests/test_ratelimit.py`

**Interfaces:**
- Consumes: nothing
- Produces: `RateLimiter(min_interval_seconds: float)` with `.wait() -> float` (returns seconds actually slept)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ratelimit.py
import time
from riskdyn.sources.d12.ratelimit import RateLimiter


def test_first_call_does_not_block():
    assert RateLimiter(0.2).wait() == 0.0


def test_subsequent_calls_are_spaced_by_min_interval():
    limiter = RateLimiter(0.2)
    start = time.monotonic()
    limiter.wait()
    limiter.wait()
    limiter.wait()
    elapsed = time.monotonic() - start
    # two enforced gaps of 0.2s; allow scheduler slack
    assert elapsed >= 0.4
    assert elapsed < 1.0


def test_no_sleep_when_enough_time_already_passed():
    limiter = RateLimiter(0.05)
    limiter.wait()
    time.sleep(0.06)
    assert limiter.wait() == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ratelimit.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# riskdyn/sources/d12/ratelimit.py
"""Minimum-interval rate limiting.

Deliberately simple and always-on: politeness toward a small community site is
a correctness property here, not a tunable.
"""
from __future__ import annotations

import time


class RateLimiter:
    def __init__(self, min_interval_seconds: float) -> None:
        self.min_interval_seconds = min_interval_seconds
        self._last_call: float | None = None

    def wait(self) -> float:
        """Block until the minimum interval has elapsed. Returns seconds slept."""
        now = time.monotonic()
        if self._last_call is None:
            self._last_call = now
            return 0.0
        elapsed = now - self._last_call
        remaining = self.min_interval_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)
            self._last_call = time.monotonic()
            return remaining
        self._last_call = now
        return 0.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ratelimit.py -v`
Expected: PASS, 3 tests

- [ ] **Step 5: Commit**

```bash
git add riskdyn/sources/d12/ratelimit.py tests/test_ratelimit.py
git commit -m "feat: always-on request rate limiter"
```

---

### Task 5: On-disk cache

**Files:**
- Create: `riskdyn/sources/d12/cache.py`
- Test: `tests/test_cache.py`

**Interfaces:**
- Consumes: nothing
- Produces: `ResponseCache(root: pathlib.Path)` with `.get(url: str) -> bytes | None`, `.put(url: str, body: bytes) -> pathlib.Path`, `.path_for(url: str) -> pathlib.Path`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cache.py
from riskdyn.sources.d12.cache import ResponseCache


def test_miss_returns_none(tmp_path):
    assert ResponseCache(tmp_path).get("https://dominating12.com/maps") is None


def test_roundtrip_returns_exact_bytes(tmp_path):
    cache = ResponseCache(tmp_path)
    body = b"\x00\x01binary\xff payload"
    cache.put("https://dominating12.com/maps", body)
    assert cache.get("https://dominating12.com/maps") == body


def test_distinct_urls_do_not_collide(tmp_path):
    cache = ResponseCache(tmp_path)
    cache.put("https://dominating12.com/maps", b"a")
    cache.put("https://dominating12.com/maps?page=2", b"b")
    assert cache.get("https://dominating12.com/maps") == b"a"
    assert cache.get("https://dominating12.com/maps?page=2") == b"b"


def test_cache_survives_a_new_instance(tmp_path):
    ResponseCache(tmp_path).put("https://dominating12.com/maps", b"persisted")
    assert ResponseCache(tmp_path).get("https://dominating12.com/maps") == b"persisted"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cache.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# riskdyn/sources/d12/cache.py
"""Content-addressed on-disk response cache.

Every fetch is cached on first retrieval so that re-analysis never re-crawls.
Cache entries are keyed by the full URL hash, and sharded two levels deep to
keep directory sizes reasonable across a large corpus.
"""
from __future__ import annotations

import hashlib
import pathlib


class ResponseCache:
    def __init__(self, root: str | pathlib.Path) -> None:
        self.root = pathlib.Path(root)

    def path_for(self, url: str) -> pathlib.Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.root / digest[:2] / digest[2:4] / digest

    def get(self, url: str) -> bytes | None:
        path = self.path_for(url)
        return path.read_bytes() if path.exists() else None

    def put(self, url: str, body: bytes) -> pathlib.Path:
        path = self.path_for(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cache.py -v`
Expected: PASS, 4 tests

- [ ] **Step 5: Commit**

```bash
git add riskdyn/sources/d12/cache.py tests/test_cache.py
git commit -m "feat: content-addressed response cache"
```

---

### Task 6: D12Client — the only network code

**Files:**
- Create: `riskdyn/sources/d12/fetch.py`
- Test: `tests/test_fetch.py`

**Interfaces:**
- Consumes: `RobotsPolicy`, `RobotsDisallowed` (Task 2); `Settings`, `PermissionRecord` (Task 3); `RateLimiter` (Task 4); `ResponseCache` (Task 5)
- Produces: `D12Client(settings: Settings | None = None, session_cookie: str | None = None)` with `.get(path: str, use_cache: bool = True) -> bytes`, `.get_text(path) -> str`, `.get_json(path) -> object`, `.close()`; attribute `.robots: RobotsPolicy`; constant `BASE_URL = "https://dominating12.com"`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_fetch.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_fetch.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'riskdyn.sources.d12.fetch'`

- [ ] **Step 3: Implement**

```python
# riskdyn/sources/d12/fetch.py
"""The only module in riskdyn that performs network I/O.

Every request passes three gates in order: robots policy (overridable only by
an explicit PermissionRecord), the cache, and the rate limiter. Keeping this
in one place means the rest of the package is trivially offline-testable.
"""
from __future__ import annotations

import json

import httpx

from riskdyn.config import Settings
from riskdyn.sources.d12.cache import ResponseCache
from riskdyn.sources.d12.ratelimit import RateLimiter
from riskdyn.sources.d12.robots import RobotsDisallowed, RobotsPolicy

BASE_URL = "https://dominating12.com"

# Captured 2026-08-10. Refreshed by `.refresh_robots()`; hardcoded so that the
# gate is active on the very first request, before any network call.
DEFAULT_ROBOTS = """User-agent: *
Disallow: /game/
Disallow: /user/
Disallow: /userlist
"""


class D12Client:
    def __init__(
        self,
        settings: Settings | None = None,
        session_cookie: str | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.robots = RobotsPolicy.parse(DEFAULT_ROBOTS)
        self.cache = ResponseCache(self.settings.cache_dir)
        interval = (
            self.settings.permission.rate_limit_seconds
            if self.settings.permission is not None
            else self.settings.rate_limit_seconds
        )
        self.limiter = RateLimiter(interval)
        # `session_cookie` is a raw Cookie *header* value, e.g.
        # "laravel_session=abc; XSRF-TOKEN=def", not a single named cookie. D12's
        # session cookie name has never been verified, so passing the header
        # through verbatim avoids guessing it. Normally None: everything phase 0-1
        # needs is reachable unauthenticated.
        headers = {"User-Agent": self.settings.user_agent}
        if session_cookie:
            headers["Cookie"] = session_cookie
        self._client = httpx.Client(
            base_url=BASE_URL,
            headers=headers,
            follow_redirects=False,
            timeout=30.0,
        )

    def _check_allowed(self, path: str) -> None:
        # NOTE: both gates canonicalize via riskdyn.paths.canonicalize_path, so
        # they agree on what `path` means. Pass BARE PATHS only ("/maps",
        # "/api/user/names?q=x"): anything carrying a host is refused outright,
        # because "//host/x" cannot be told from a path safely.
        if self.robots.is_allowed(path):
            return
        permission = self.settings.permission
        if permission is not None and permission.covers(path):
            return
        raise RobotsDisallowed(
            f"{path!r} is disallowed by robots.txt and is not covered by a "
            f"configured permission record. Obtain written permission from the "
            f"site operator and record it via PermissionRecord before fetching."
        )

    def get(self, path: str, use_cache: bool = True) -> bytes:
        self._check_allowed(path)
        url = f"{BASE_URL}{path}"
        if use_cache:
            cached = self.cache.get(url)
            if cached is not None:
                return cached
        self.limiter.wait()
        response = self._client.get(path)
        response.raise_for_status()
        body = response.content
        self.cache.put(url, body)
        return body

    def get_text(self, path: str, use_cache: bool = True) -> str:
        return self.get(path, use_cache=use_cache).decode("utf-8", errors="replace")

    def get_json(self, path: str, use_cache: bool = True):
        return json.loads(self.get_text(path, use_cache=use_cache))

    def refresh_robots(self) -> RobotsPolicy:
        """Re-read robots.txt from the live site and adopt it."""
        self.limiter.wait()
        response = self._client.get("/robots.txt")
        response.raise_for_status()
        self.robots = RobotsPolicy.parse(response.text)
        return self.robots

    def close(self) -> None:
        self._client.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_fetch.py -v`
Expected: PASS, 5 tests. Takes ~10s because the rate limiter is live.

Then confirm the offline suite is genuinely offline:
Run: `pytest -m "not network" -v`
Expected: PASS with no network access.

- [ ] **Step 5: Commit**

```bash
git add riskdyn/sources/d12/fetch.py tests/test_fetch.py
git commit -m "feat: D12Client with robots gate, cache, and rate limiting"
```

---

### Task 7: Map catalog parser

**Files:**
- Create: `riskdyn/maps/model.py`, `riskdyn/sources/d12/parse_catalog.py`, `tests/fixtures/maps_page.html`
- Test: `tests/test_parse_catalog.py`

**Interfaces:**
- Consumes: nothing at runtime (pure parser)
- Produces: `MapSummary` dataclass with fields `map_id: int, name: str, width: int, height: int, num_territories: int, num_regions: int, num_games_total: int, num_games_recent: int, caps: int, image_url: str, thumbnail_url: str, size: str, recommended_min_players: int, recommended_max_players: int`; `parse_catalog(html: str) -> list[MapSummary]`

- [ ] **Step 1: Capture the real fixture**

```bash
mkdir -p tests/fixtures
python -c "
from riskdyn.config import Settings
from riskdyn.sources.d12.fetch import D12Client
c = D12Client()
open('tests/fixtures/maps_page.html','w').write(c.get_text('/maps'))
c.close()
"
grep -c 'new CreateGame(' tests/fixtures/maps_page.html   # expect 1
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_parse_catalog.py
from riskdyn.sources.d12.parse_catalog import parse_catalog


def test_parses_every_map_in_the_catalog(fixtures_dir):
    maps = parse_catalog((fixtures_dir / "maps_page.html").read_text())
    assert len(maps) >= 70          # 77 at capture time; the site adds maps
    assert len({m.map_id for m in maps}) == len(maps)


def test_world_classic_fields_match_the_site(fixtures_dir):
    maps = parse_catalog((fixtures_dir / "maps_page.html").read_text())
    wc = next(m for m in maps if m.map_id == 1)
    assert wc.name == "World Classic"
    assert wc.num_territories == 42
    assert wc.num_regions == 6
    assert (wc.width, wc.height) == (1021, 689)
    assert wc.num_games_total > 500_000
    assert wc.image_url.endswith("/1.large.jpg")


def test_territory_counts_are_plausible(fixtures_dir):
    maps = parse_catalog((fixtures_dir / "maps_page.html").read_text())
    assert all(24 <= m.num_territories <= 200 for m in maps)
    # NOT `>= 1`: "Brecourt Manor" (map 77) genuinely has 0 regions — a variant
    # with no continent bonuses, 34 territories and 4,035 games played. Region
    # count ranges 0-36 across the catalog.
    assert all(0 <= m.num_regions <= 40 for m in maps)


def test_regionless_map_is_represented_not_rejected(fixtures_dir):
    maps = parse_catalog((fixtures_dir / "maps_page.html").read_text())
    brecourt = next(m for m in maps if m.name == "Brecourt Manor")
    assert brecourt.num_regions == 0
    assert brecourt.num_territories == 34


def test_raises_on_html_without_a_catalog():
    import pytest
    with pytest.raises(ValueError, match="catalog"):
        parse_catalog("<html><body>no catalog here</body></html>")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_parse_catalog.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Implement**

```python
# riskdyn/maps/model.py
"""Core map types."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MapSummary:
    """Catalog metadata for one map, as listed on /maps."""

    map_id: int
    name: str
    width: int
    height: int
    num_territories: int
    num_regions: int
    num_games_total: int
    num_games_recent: int
    caps: int
    image_url: str
    thumbnail_url: str
    size: str
    recommended_min_players: int
    recommended_max_players: int
```

```python
# riskdyn/sources/d12/parse_catalog.py
"""Parse the map catalog embedded in the /maps page.

The page ships the whole catalog as a JSON literal inside a
``new CreateGame({...}, true)`` call, so there is no HTML scraping involved and
no per-map request needed.
"""
from __future__ import annotations

import json

from riskdyn.maps.model import MapSummary

_MARKER = "new CreateGame("


def parse_catalog(html: str) -> list[MapSummary]:
    start = html.find(_MARKER)
    if start == -1:
        raise ValueError("no map catalog found in page (missing CreateGame call)")
    payload_start = start + len(_MARKER)
    catalog, _ = json.JSONDecoder().raw_decode(html[payload_start:])
    return [
        MapSummary(
            map_id=int(entry["map_id"]),
            name=entry["name"],
            width=int(entry["width"]),
            height=int(entry["height"]),
            num_territories=int(entry["num_territories"]),
            num_regions=int(entry["num_regions"]),
            num_games_total=int(entry.get("num_games_total", 0)),
            num_games_recent=int(entry.get("num_games_recent", 0)),
            caps=int(entry.get("caps", 0)),
            image_url=entry["imageUrl"],
            thumbnail_url=entry["imageThumbnailUrl"],
            size=entry.get("size", ""),
            recommended_min_players=int(entry.get("recommended_min_players", 0)),
            recommended_max_players=int(entry.get("recommended_max_players", 0)),
        )
        for entry in catalog.values()
    ]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_parse_catalog.py -v`
Expected: PASS, 4 tests

- [ ] **Step 6: Commit**

```bash
git add riskdyn/maps/model.py riskdyn/sources/d12/parse_catalog.py tests/
git commit -m "feat: parse the 77-map D12 catalog from the /maps page"
```

---

### Task 8: Topology model and parser

**Files:**
- Modify: `riskdyn/maps/model.py`
- Create: `riskdyn/sources/d12/parse_topology.py`
- Test: `tests/test_parse_topology.py`
- Fixture (ALREADY CAPTURED, committed): `tests/fixtures/game_map1_territories.html`

**Interfaces:**
- Consumes: `MapSummary` (Task 7)
- Produces: `Territory(territory_id: int, name: str, region_id: int, x: int, y: int, adjacencies: tuple[int, ...])`; `MapTopology(map_id: int, territories: tuple[Territory, ...])` with `.by_id: dict[int, Territory]`; `parse_topology(html: str, map_id: int) -> MapTopology`

**REVISED 2026-08-10 against real markup.** The original brief guessed at attribute names from
`bundle.js`. A real page has since been captured and the guesses were wrong. Corrections:

| assumed | actual |
|-|-|
| `data-territory-id` | **`data-territory`** |
| `data-region-id` | **does not exist** — continent membership is absent from the markup entirely |
| topology at `/mappanel/map/<id>` | `/mappanel/map/<id>` is not a real route; topology is on `/game/<id>` |
| territory name in `title` | **`data-name`** |

Real element, verbatim:

```html
<a href="#5" id="territory-5" class="js_territory" data-territory="5"
   data-adjacencies="7,11,66,69" data-x="92" data-y="68" data-name="Northwest Territory">
```

Continent membership must come from a separate source — see issue #4 (map image segmentation).
`region_id` therefore stays on the model but defaults to `0`, which `check_invariants` already
treats as a legitimate region-less map (the Brecourt Manor case in Task 9).

- [ ] **Step 1: Confirm the fixture is present**

The fixture is already captured and committed. It is a scrubbed, topology-only extract of a real
`/game/1889` page (World Classic): the 42 territory anchors with their `data-*` attributes, and
nothing else — no scripts, no session tokens, no account identifiers, no chat.

```bash
test -f tests/fixtures/game_map1_territories.html || { echo "BLOCKED: fixture missing"; exit 1; }
grep -c 'data-adjacencies' tests/fixtures/game_map1_territories.html   # expect 42
```

Do NOT fetch anything from D12 in this task, and do not fabricate additional fixtures.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_parse_topology.py
import pytest

from riskdyn.sources.d12.parse_topology import parse_topology

FIXTURE_NAME = "game_map1_territories.html"


@pytest.fixture
def world_classic(fixtures_dir):
    return parse_topology((fixtures_dir / FIXTURE_NAME).read_text(), map_id=1)


def test_world_classic_has_42_territories(world_classic):
    assert len(world_classic.territories) == 42


def test_adjacency_is_symmetric(world_classic):
    for t in world_classic.territories:
        for neighbour_id in t.adjacencies:
            assert t.territory_id in world_classic.by_id[neighbour_id].adjacencies, (
                f"{t.territory_id} -> {neighbour_id} is not reciprocated"
            )


def test_world_classic_has_83_edges(world_classic):
    # Verified against the real page: 42 territories, 83 undirected borders.
    edges = sum(len(t.adjacencies) for t in world_classic.territories)
    assert edges % 2 == 0
    assert edges // 2 == 83


def test_every_adjacency_refers_to_a_known_territory(world_classic):
    known = set(world_classic.by_id)
    for t in world_classic.territories:
        assert set(t.adjacencies) <= known, f"{t.territory_id} cites unknown neighbours"


def test_every_territory_has_a_name_and_coordinates(world_classic):
    # NOTE: region_id is deliberately NOT asserted here. D12's markup carries no
    # continent membership at all (see the revision table above); it defaults to 0.
    assert all(t.name for t in world_classic.territories)
    assert all(t.x > 0 and t.y > 0 for t in world_classic.territories)


def test_known_territory_names_are_parsed(world_classic):
    names = {t.name for t in world_classic.territories}
    assert {"Northwest Territory", "Ontario", "Greenland", "Kamchatka"} <= names


def test_no_territory_is_isolated(world_classic):
    assert all(t.adjacencies for t in world_classic.territories)


def test_raises_on_markup_without_territories():
    with pytest.raises(ValueError, match="territory"):
        parse_topology("<html><body>nothing here</body></html>", map_id=1)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_parse_topology.py -v`
Expected: FAIL with `ModuleNotFoundError` (or SKIP if no fixture — in which case stop and report)

- [ ] **Step 4: Implement**

Append to `riskdyn/maps/model.py`:

```python
@dataclass(frozen=True)
class Territory:
    territory_id: int
    name: str
    region_id: int
    x: int
    y: int
    adjacencies: tuple[int, ...]


@dataclass(frozen=True)
class MapTopology:
    map_id: int
    territories: tuple[Territory, ...]

    @property
    def by_id(self) -> dict[int, "Territory"]:
        return {t.territory_id: t for t in self.territories}
```

```python
# riskdyn/sources/d12/parse_topology.py
"""Extract territory topology from D12 page markup.

Territory anchors carry everything the markup exposes as data attributes:
``data-territory`` (the id), ``data-name``, ``data-x``, ``data-y``, and
``data-adjacencies`` (a comma-separated list of territory ids).

Continent membership is NOT present anywhere in D12's markup, so ``region_id``
defaults to 0 and must be supplied from another source — see issue #4.
"""
from __future__ import annotations

import html as html_module
import re

from riskdyn.maps.model import MapTopology, Territory

_ELEMENT = re.compile(r"<a\b[^>]*\bdata-adjacencies\s*=\s*\"[^\"]*\"[^>]*>")
_ATTR = re.compile(r"\bdata-([a-z\-]+)\s*=\s*\"([^\"]*)\"")


def parse_topology(html: str, map_id: int) -> MapTopology:
    territories: list[Territory] = []
    for element in _ELEMENT.findall(html):
        attrs = dict(_ATTR.findall(element))
        if "territory" not in attrs:
            continue
        raw_adj = attrs.get("adjacencies", "").strip()
        adjacencies = tuple(
            int(part) for part in raw_adj.split(",") if part.strip()
        )
        territories.append(
            Territory(
                territory_id=int(attrs["territory"]),
                name=html_module.unescape(attrs.get("name", "")),
                # D12 exposes no continent membership; 0 means "unknown region".
                region_id=int(attrs.get("region", 0)),
                x=int(float(attrs.get("x", 0))),
                y=int(float(attrs.get("y", 0))),
                adjacencies=adjacencies,
            )
        )
    if not territories:
        raise ValueError("no territory elements found (missing data-adjacencies)")
    return MapTopology(map_id=map_id, territories=tuple(territories))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_parse_topology.py -v`
Expected: PASS, 4 tests

If `test_adjacency_is_symmetric` fails, **do not relax the test.** Asymmetric adjacency means either the parser is dropping entries or D12 genuinely encodes one-way connections on some maps. Investigate which, and report before changing anything.

- [ ] **Step 6: Commit**

```bash
git add riskdyn/maps/model.py riskdyn/sources/d12/parse_topology.py tests/
git commit -m "feat: parse territory topology from data-adjacencies markup"
```

---

### Task 9: Map graph construction and invariants

**Files:**
- Create: `riskdyn/maps/graph.py`
- Test: `tests/test_maps_graph.py`

**Interfaces:**
- Consumes: `MapTopology`, `Territory` (Task 8)
- Produces: `to_graph(topology: MapTopology) -> networkx.Graph` (nodes are territory ids, node attrs `name`, `region_id`, `x`, `y`); `check_invariants(graph) -> list[str]` (returns violation messages, empty when clean); `region_subgraphs(graph) -> dict[int, networkx.Graph]`

- [ ] **Step 1: Write the failing test**

Uses a hand-built topology so the test does not depend on the gated fixture.

```python
# tests/test_maps_graph.py
import networkx as nx
import pytest
from riskdyn.maps.graph import check_invariants, region_subgraphs, to_graph
from riskdyn.maps.model import MapTopology, Territory


def triangle() -> MapTopology:
    return MapTopology(map_id=99, territories=(
        Territory(1, "A", 1, 10, 10, (2, 3)),
        Territory(2, "B", 1, 20, 10, (1, 3)),
        Territory(3, "C", 2, 30, 10, (1, 2)),
    ))


def test_graph_has_a_node_per_territory():
    g = to_graph(triangle())
    assert set(g.nodes) == {1, 2, 3}
    assert g.nodes[1]["name"] == "A"
    assert g.nodes[3]["region_id"] == 2


def test_edges_are_undirected_and_deduplicated():
    g = to_graph(triangle())
    assert g.number_of_edges() == 3
    assert g.has_edge(1, 2) and g.has_edge(2, 1)


def test_clean_topology_reports_no_violations():
    assert check_invariants(to_graph(triangle())) == []


def test_disconnected_map_is_reported():
    topo = MapTopology(map_id=99, territories=(
        Territory(1, "A", 1, 0, 0, (2,)),
        Territory(2, "B", 1, 1, 0, (1,)),
        Territory(3, "island", 2, 9, 9, ()),
    ))
    violations = check_invariants(to_graph(topo))
    assert any("connected" in v for v in violations)


def test_self_loop_is_reported():
    topo = MapTopology(map_id=99, territories=(
        Territory(1, "A", 1, 0, 0, (1, 2)),
        Territory(2, "B", 1, 1, 0, (1,)),
    ))
    assert any("self-loop" in v for v in check_invariants(to_graph(topo)))


def test_region_subgraphs_partition_the_territories():
    subs = region_subgraphs(to_graph(triangle()))
    assert set(subs) == {1, 2}
    assert sum(s.number_of_nodes() for s in subs.values()) == 3


def test_map_with_no_regions_is_not_a_violation():
    # "Brecourt Manor" (map 77) has 34 territories and 0 regions — a variant
    # with no continent bonuses. Every territory has region_id 0, and that must
    # not produce 34 spurious violations.
    topo = MapTopology(map_id=77, territories=(
        Territory(1, "A", 0, 0, 0, (2,)),
        Territory(2, "B", 0, 1, 0, (1, 3)),
        Territory(3, "C", 0, 2, 0, (2,)),
    ))
    assert check_invariants(to_graph(topo)) == []


def test_partial_region_assignment_is_a_violation():
    # A map where *some* territories have regions and others do not indicates a
    # parse failure, unlike a map with no regions at all.
    topo = MapTopology(map_id=99, territories=(
        Territory(1, "A", 1, 0, 0, (2,)),
        Territory(2, "B", 0, 1, 0, (1, 3)),
        Territory(3, "C", 2, 2, 0, (2,)),
    ))
    assert any("without a region" in v for v in check_invariants(to_graph(topo)))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_maps_graph.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# riskdyn/maps/graph.py
"""Territory adjacency as a networkx graph.

This is the structural representation the position-strength metrics and the
map-level graph metrics are built on, so its invariants are checked explicitly
rather than assumed.
"""
from __future__ import annotations

import networkx as nx

from riskdyn.maps.model import MapTopology


def to_graph(topology: MapTopology) -> nx.Graph:
    graph = nx.Graph()
    for territory in topology.territories:
        graph.add_node(
            territory.territory_id,
            name=territory.name,
            region_id=territory.region_id,
            x=territory.x,
            y=territory.y,
        )
    for territory in topology.territories:
        for neighbour_id in territory.adjacencies:
            graph.add_edge(territory.territory_id, neighbour_id)
    return graph


def check_invariants(graph: nx.Graph) -> list[str]:
    """Return a list of invariant violations; empty means the map is well formed."""
    violations: list[str] = []

    self_loops = list(nx.selfloop_edges(graph))
    if self_loops:
        violations.append(f"self-loop edges present: {self_loops}")

    if graph.number_of_nodes() and not nx.is_connected(graph):
        components = sorted(
            (sorted(c) for c in nx.connected_components(graph)), key=len, reverse=True
        )
        violations.append(
            f"graph is not connected: {len(components)} components, "
            f"smallest {components[-1]}"
        )

    isolated = [n for n, d in graph.degree if d == 0]
    if isolated:
        violations.append(f"isolated territories: {isolated}")

    # Some maps legitimately have no regions at all — "Brecourt Manor" is a
    # 34-territory variant with no continent bonuses. Only a *partial* region
    # assignment indicates a parse failure, so check for that rather than for
    # the absence of regions.
    region_ids = [data.get("region_id", 0) for _, data in graph.nodes(data=True)]
    if any(region_ids) and not all(region_ids):
        missing = [
            n for n, data in graph.nodes(data=True) if not data.get("region_id")
        ]
        violations.append(f"territories without a region: {missing}")

    return violations


def region_subgraphs(graph: nx.Graph) -> dict[int, nx.Graph]:
    """One induced subgraph per region (continent)."""
    regions: dict[int, list[int]] = {}
    for node, data in graph.nodes(data=True):
        regions.setdefault(data["region_id"], []).append(node)
    return {rid: graph.subgraph(nodes).copy() for rid, nodes in regions.items()}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_maps_graph.py -v`
Expected: PASS, 6 tests

- [ ] **Step 5: Commit**

```bash
git add riskdyn/maps/graph.py tests/test_maps_graph.py
git commit -m "feat: adjacency graph construction with explicit invariant checks"
```

---

### Task 10: Vector map rendering

**Files:**
- Create: `riskdyn/maps/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `MapTopology` (Task 8), `to_graph` (Task 9)
- Produces: `render_map(topology: MapTopology, out_path, *, width=None, height=None, title=None, colour_by_region=True) -> pathlib.Path`

Issue #1 asks for "a high quality vector (plottable at any resolution) with labeled territories and connections." D12 ships only raster map images, so the vector rendering is **ours**, drawn from `data-x`/`data-y` coordinates and the adjacency graph. Node positions use the site's own label coordinates so the rendering is geographically faithful. Note that D12's y axis points down (screen coordinates), so it is inverted for plotting.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_render.py
import pytest
from riskdyn.maps.model import MapTopology, Territory
from riskdyn.maps.render import render_map


def square() -> MapTopology:
    return MapTopology(map_id=99, territories=(
        Territory(1, "Alpha", 1, 10, 10, (2, 3)),
        Territory(2, "Beta", 1, 90, 10, (1, 4)),
        Territory(3, "Gamma", 2, 10, 90, (1, 4)),
        Territory(4, "Delta", 2, 90, 90, (2, 3)),
    ))


@pytest.mark.parametrize("suffix", [".pdf", ".svg", ".png"])
def test_renders_each_format(tmp_path, suffix):
    out = render_map(square(), tmp_path / f"map{suffix}", width=1021, height=689)
    assert out.exists()
    assert out.stat().st_size > 1000


def test_pdf_output_is_vector_not_raster(tmp_path):
    out = render_map(square(), tmp_path / "map.pdf", width=1021, height=689)
    body = out.read_bytes()
    assert body.startswith(b"%PDF")
    # A vector PDF contains no embedded image XObject for this content.
    assert b"/Subtype /Image" not in body


def test_svg_contains_every_territory_label(tmp_path):
    out = render_map(square(), tmp_path / "map.svg", width=1021, height=689)
    svg = out.read_text()
    for name in ("Alpha", "Beta", "Gamma", "Delta"):
        assert name in svg
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_render.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# riskdyn/maps/render.py
"""Vector rendering of a map's territories and connections.

D12 distributes maps as raster JPEGs, so this module produces our own vector
representation from the site's territory label coordinates plus the adjacency
graph. Output is resolution-independent (PDF/SVG), which is what the paper's
figure pipeline needs.
"""
from __future__ import annotations

import pathlib

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from riskdyn.maps.graph import to_graph  # noqa: E402
from riskdyn.maps.model import MapTopology  # noqa: E402

# Region counts run 0-36 across the D12 catalog, so a single 20-colour
# qualitative map is not enough. tab20 + tab20b + tab20c gives 60 distinct
# categorical colours. Do NOT use `.resampled(n)` here: that samples *across*
# a colormap rather than taking its discrete entries, and for small n returns
# near-identical shades (resampled(2) on tab20 yields two similar blues).
REGION_PALETTE = (
    tuple(matplotlib.colormaps["tab20"].colors)
    + tuple(matplotlib.colormaps["tab20b"].colors)
    + tuple(matplotlib.colormaps["tab20c"].colors)
)


def render_map(
    topology: MapTopology,
    out_path: str | pathlib.Path,
    *,
    width: int | None = None,
    height: int | None = None,
    title: str | None = None,
    colour_by_region: bool = True,
) -> pathlib.Path:
    out_path = pathlib.Path(out_path)
    graph = to_graph(topology)

    # D12 uses screen coordinates (y grows downward); invert for plotting.
    positions = {
        node: (data["x"], -data["y"]) for node, data in graph.nodes(data=True)
    }

    region_ids = sorted({data["region_id"] for _, data in graph.nodes(data=True)})
    colour_for = {rid: REGION_PALETTE[i % len(REGION_PALETTE)]
                  for i, rid in enumerate(region_ids)}

    aspect = (width / height) if (width and height) else 1.5
    fig, ax = plt.subplots(figsize=(12, 12 / aspect))

    for source, target in graph.edges:
        x0, y0 = positions[source]
        x1, y1 = positions[target]
        ax.plot([x0, x1], [y0, y1], color="0.55", linewidth=0.8, zorder=1)

    for node, data in graph.nodes(data=True):
        x, y = positions[node]
        colour = colour_for[data["region_id"]] if colour_by_region else "white"
        ax.scatter([x], [y], s=260, color=colour, edgecolors="black",
                   linewidths=0.8, zorder=2)
        ax.annotate(
            data["name"] or str(node), (x, y),
            textcoords="offset points", xytext=(0, 12),
            ha="center", fontsize=7, zorder=3,
        )

    if title:
        ax.set_title(title)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(out_path, transparent=True, bbox_inches="tight")
    plt.close(fig)
    return out_path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_render.py -v`
Expected: PASS, 5 tests

- [ ] **Step 5: Visually verify (required by repo policy)**

```bash
python -c "
from riskdyn.maps.model import MapTopology, Territory
from riskdyn.maps.render import render_map
topo = MapTopology(99, (
    Territory(1,'Alpha',1,10,10,(2,3)), Territory(2,'Beta',1,90,10,(1,4)),
    Territory(3,'Gamma',2,10,90,(1,4)), Territory(4,'Delta',2,90,90,(2,3))))
render_map(topo, '/tmp/riskdyn_check.png', width=1021, height=689, title='render check')
"
```
Open `/tmp/riskdyn_check.png` and confirm: four labelled nodes, two colours, four edges, no clipped labels. Delete it afterwards.

- [ ] **Step 6: Commit**

```bash
git add riskdyn/maps/render.py tests/test_render.py
git commit -m "feat: resolution-independent vector map rendering"
```

---

### Task 11: CLI for catalog and image retrieval

**Files:**
- Create: `riskdyn/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `D12Client` (Task 6), `parse_catalog` (Task 7)
- Produces: `main(argv: list[str] | None = None) -> int`; subcommands `pull-catalog --out PATH`, `pull-images --out DIR [--limit N]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# riskdyn/cli.py
"""Command-line entry points for data retrieval."""
from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import sys

from riskdyn.sources.d12.fetch import D12Client
from riskdyn.sources.d12.parse_catalog import parse_catalog


def _pull_catalog(out: pathlib.Path) -> int:
    client = D12Client()
    try:
        maps = parse_catalog(client.get_text("/maps"))
    finally:
        client.close()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([dataclasses.asdict(m) for m in maps], indent=1))
    print(f"wrote {len(maps)} maps to {out}")
    return 0


def _pull_images(out_dir: pathlib.Path, limit: int | None) -> int:
    client = D12Client()
    try:
        maps = parse_catalog(client.get_text("/maps"))
        maps.sort(key=lambda m: -m.num_games_total)
        if limit is not None:
            maps = maps[:limit]
        out_dir.mkdir(parents=True, exist_ok=True)
        for summary in maps:
            path = f"/assets/img/maps/{summary.map_id}.large.jpg"
            (out_dir / f"{summary.map_id}.large.jpg").write_bytes(client.get(path))
            print(f"  {summary.map_id:>3}  {summary.name}")
    finally:
        client.close()
    print(f"wrote {len(maps)} images to {out_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="riskdyn")
    sub = parser.add_subparsers(dest="command")

    catalog = sub.add_parser("pull-catalog", help="fetch the map catalog")
    catalog.add_argument("--out", type=pathlib.Path, default=pathlib.Path("data/raw/map_catalog.json"))

    images = sub.add_parser("pull-images", help="fetch map images, most-played first")
    images.add_argument("--out", type=pathlib.Path, default=pathlib.Path("data/raw/map_images"))
    images.add_argument("--limit", type=int, default=None)

    args = parser.parse_args(argv)
    if args.command == "pull-catalog":
        return _pull_catalog(args.out)
    if args.command == "pull-images":
        return _pull_images(args.out, args.limit)
    parser.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -v`
Expected: PASS, 2 tests

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: all PASS (topology tests SKIP if the gated fixture is absent)

- [ ] **Step 6: Commit**

```bash
git add riskdyn/cli.py tests/test_cli.py
git commit -m "feat: CLI for catalog and map image retrieval"
```

---

## Blocked work — do not attempt in this plan

| item | blocked on |
|-|-|
| username → numeric user id for the 12 seed players | `/userlist` and `/user/<id>` are robots-disallowed; `/api/user/names` returns names only |
| running Task 8 at scale across all 77 maps | authenticated session, plus confirmation that `/mappanel/map/<id>` is viewable by non-author accounts |
| everything in spec phases 2–6 | permission reply, sent 2026-08-10 |

**One manual check would unblock Task 8:** log in to D12 and open `https://dominating12.com/mappanel/map/1`. If the territory list renders, a session cookie is sufficient and Task 8 proceeds. If it 403s or redirects, map topology needs to be requested explicitly in the permission thread.
