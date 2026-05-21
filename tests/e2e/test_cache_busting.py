"""Cache hygiene regression net — pins issue #13's four invariants.

#15 reorganized ``static_versioning`` and moved the static mount into a
slimmed ``server.py``; this guards that the cache-hygiene guarantees
survived the move:

1. ``/`` is always revalidated — Safari (especially PWA-installed) used
   to serve a stale ``index.html`` referencing a ``?v=<old hash>`` script
   that no longer existed.
2. ``/static/*.{js,css}`` is immutable for a year, so the cache bust
   actually pays off.
3. The ``?v=<hash>`` stamped into ``index.html`` matches the on-disk
   content hash — *this* is the test that catches "edited a JS file but
   the served stamp is stale".
4. ``/api/version`` returns the keys the Settings build-line relies on,
   and its ``asset_hash`` matches the ``app.js`` stamp.

Non-browser: plain ``requests`` against the live server. Parametrising
over both Playwright projections would just hit the same loopback URLs
twice, so the suite runs it once under the chromium projection.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import requests

from src.static_versioning import asset_hash

pytestmark = pytest.mark.smoke

_STATIC_DIR = (
    Path(__file__).resolve().parents[2] / "app" / "webapp" / "static"
)


@pytest.fixture
def once(browser_name: str) -> None:
    """Run this non-browser test once, not once per engine projection."""
    if browser_name != "chromium":
        pytest.skip("non-browser test — runs once under the chromium projection")


def _get(base_url: str, path: str) -> requests.Response:
    return requests.get(f"{base_url}{path}", verify=False, timeout=5)


def test_index_revalidates(once: None, base_url: str) -> None:
    res = _get(base_url, "/")
    assert res.status_code == 200
    cc = res.headers.get("cache-control", "")
    assert "no-cache" in cc, f"index.html must revalidate; got Cache-Control: {cc!r}"


def test_static_assets_are_long_cached(once: None, base_url: str) -> None:
    for asset in ("app.js", "styles.css"):
        cc = _get(base_url, f"/static/{asset}").headers.get("cache-control", "")
        assert "max-age=31536000" in cc and "immutable" in cc, (
            f"{asset} must be immutably cached; got {cc!r}"
        )


def test_index_stamps_match_on_disk(once: None, base_url: str) -> None:
    html = _get(base_url, "/").text
    # The placeholders must have been substituted at render time.
    assert "__APP_JS__" not in html and "__STYLES_CSS__" not in html
    for asset in ("app.js", "styles.css"):
        match = re.search(
            rf"/static/{re.escape(asset)}\?v=([0-9a-f]{{8}})", html
        )
        assert match, f"{asset} is not content-hash stamped in index.html"
        expected = asset_hash(_STATIC_DIR / asset)
        assert match.group(1) == expected, (
            f"{asset} stamp {match.group(1)} diverges from the on-disk "
            f"content hash {expected} — a stale deploy or a missed bust"
        )


def test_version_endpoint_matches_app_js_stamp(once: None, base_url: str) -> None:
    version = _get(base_url, "/api/version").json()
    for key in ("git_sha", "built_at", "asset_hash"):
        value = version.get(key)
        assert isinstance(value, str) and value, f"/api/version missing {key}"
    html = _get(base_url, "/").text
    match = re.search(r"/static/app\.js\?v=([0-9a-f]{8})", html)
    assert match and match.group(1) == version["asset_hash"], (
        "/api/version asset_hash must equal the app.js stamp in index.html"
    )
