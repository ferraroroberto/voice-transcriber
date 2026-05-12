"""Bridge tests for the static JS.

Vitest tests live in `app/webapp/static/__tests__/` but require Node.js.
On a Python-only CI box they're skipped — so this file pins the
behaviour from the Python side too:

1. **Source pins** — the JS file still contains the expected functions
   and key tokens. Catches accidental deletions or renames during
   refactors.
2. **Parity port** — a Python re-implementation of `polishModelLabel`
   is exercised against the same expected outputs the Vitest suite
   asserts. Whenever the JS rule changes (e.g. switch to camelCase),
   update BOTH the Vitest file and the Python port here.
"""

from __future__ import annotations

# Standard library imports
import re
import shutil
from pathlib import Path

# Third-party imports
import pytest


STATIC_DIR = Path(__file__).resolve().parent.parent / "app" / "webapp" / "static"


# ---------------------------------------------------------------------------
# Source pins — fail loudly if the JS structure changes without warning.
# ---------------------------------------------------------------------------

@pytest.fixture
def app_js() -> str:
    target = STATIC_DIR / "app.js"
    if not target.exists():
        pytest.skip("app.js missing")
    return target.read_text(encoding="utf-8")


class TestAppJsSourcePins:
    def test_polish_model_label_function_present(self, app_js: str):
        assert "function polishModelLabel" in app_js

    def test_label_function_uses_underscore_split(self, app_js: str):
        # The whole point of the refactor: split on '_', not look up
        # in a hardcoded map. If a future refactor swaps split() for a
        # map lookup, this assertion fires.
        match = re.search(
            r"function polishModelLabel\([^\)]*\)\s*\{(.*?)\}",
            app_js,
            re.DOTALL,
        )
        assert match is not None, "polishModelLabel body not found"
        body = match.group(1)
        assert ".split('_')" in body or '.split("_")' in body
        assert "toUpperCase" in body

    def test_no_hardcoded_alias_list_in_offline_fallback(self, app_js: str):
        """`applyConfigDefaults` should not hardcode the alias list — the
        offline fallback should be empty so the model list always comes
        from /api/config (which reads the JSON sample). The webapp
        team's earlier check-in had a hardcoded array here; this test
        guards against regressing to that shape."""
        # Find the applyConfigDefaults block.
        match = re.search(
            r"function applyConfigDefaults\([^\)]*\)\s*\{(.*?)\n  \}",
            app_js,
            re.DOTALL,
        )
        assert match is not None, "applyConfigDefaults body not found"
        body = match.group(1)
        # No alias strings in the body. We allow them anywhere else in
        # app.js (e.g. in tests, comments) but not in this fallback.
        forbidden = ["claude_haiku", "claude_sonnet", "claude_opus",
                     "gemini_lite", "gemini_flash", "gemini_pro"]
        for token in forbidden:
            assert token not in body, (
                f"applyConfigDefaults must not hardcode {token!r} — "
                f"the offline fallback should be empty so the model "
                f"list always comes from the server."
            )


# ---------------------------------------------------------------------------
# Python parity port — runnable proof the rule is correct.
# ---------------------------------------------------------------------------

def polish_model_label_py(model_id) -> str:
    """Python mirror of the JS function in app.js. Keep in sync; the
    Vitest suite asserts the same expectations on the JS side."""
    text = str(model_id or "")
    parts = [seg for seg in text.split("_") if seg]
    return " ".join(seg[0].upper() + seg[1:] for seg in parts)


class TestPolishModelLabelParity:
    @pytest.mark.parametrize("inp,expected", [
        ("claude_haiku",  "Claude Haiku"),
        ("claude_sonnet", "Claude Sonnet"),
        ("claude_opus",   "Claude Opus"),
        ("gemini_lite",   "Gemini Lite"),
        ("gemini_flash",  "Gemini Flash"),
        ("gemini_pro",    "Gemini Pro"),
        ("gemini_2_flash", "Gemini 2 Flash"),
        ("brand_new_model", "Brand New Model"),
        ("whisper", "Whisper"),
        ("", ""),
        (None, ""),
        ("foo__bar", "Foo Bar"),
        ("_foo", "Foo"),
        ("foo_", "Foo"),
    ])
    def test_matches_vitest_expectations(self, inp, expected):
        assert polish_model_label_py(inp) == expected


# ---------------------------------------------------------------------------
# Optional: actually run Vitest when Node is available.
# ---------------------------------------------------------------------------

class TestVitestSuite:
    def test_vitest_runs_if_node_present(self):
        """Best-effort: if Node + Vitest are installed, run the JS
        suite. Otherwise skip gracefully — Node isn't a hard
        dependency."""
        if shutil.which("node") is None or shutil.which("npx") is None:
            pytest.skip("Node.js not on PATH — install Node to run JS tests")
        node_modules = STATIC_DIR.parent.parent.parent / "node_modules" / "vitest"
        if not node_modules.exists():
            pytest.skip(
                "Vitest not installed — run `npm install` from repo root"
            )
        import subprocess
        result = subprocess.run(
            ["npx", "vitest", "run", "app/webapp/static/__tests__/"],
            cwd=str(STATIC_DIR.parent.parent.parent),
            capture_output=True,
            text=True,
            shell=True,  # npx on Windows needs cmd resolution
        )
        if result.returncode != 0:
            pytest.fail(
                f"Vitest failed:\nSTDOUT:\n{result.stdout}\n"
                f"STDERR:\n{result.stderr}"
            )
