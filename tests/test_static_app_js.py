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
    """The full webapp JS source — every ES module under static/
    concatenated. ``app.js`` was split into focused modules (issue #15),
    so these source pins scan the whole module graph, not one file."""
    modules = sorted(STATIC_DIR.glob("*.js"))
    if not modules:
        pytest.skip("no JS modules found under static/")
    return "\n".join(m.read_text(encoding="utf-8") for m in modules)


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

    def test_background_finalize_wired(self, app_js: str):
        """Backgrounding the app mid-record must finalise the take rather
        than letting it die silently. Pins the function and its wiring
        into the visibilitychange / pagehide handlers (issue #12)."""
        assert "function finalizeForBackground" in app_js
        # The visibilitychange handler must call it when a recording is
        # in flight, and pagehide must make the same best-effort call.
        assert app_js.count("finalizeForBackground()") >= 2, (
            "finalizeForBackground must be called from both the "
            "visibilitychange and pagehide handlers"
        )
        # The /finish request carries keepalive so it can outlive an iOS
        # page freeze when the take is finalised due to backgrounding.
        assert "keepalive: state.backgroundFinalized" in app_js

    def test_resume_button_wired(self, app_js: str):
        """The ▶ Resume button continues a background-finalised take by
        force-appending, regardless of the ➕ Append toggle (issue #14)."""
        assert "function resumeRecording" in app_js
        assert "function appendActive" in app_js
        assert "state.forceAppend" in app_js
        assert "showResumeButton" in app_js
        assert "hideResumeButton" in app_js

    def test_chunk_upload_retries_before_dropping(self, app_js: str):
        """A failed /chunk POST must be retried with backoff, not silently
        dropped — the first chunk carries the WebM header, so losing it
        makes the whole take unparseable by ffmpeg (issue #192)."""
        assert "CHUNK_RETRY_DELAYS_MS" in app_js
        match = re.search(
            r"function enqueueChunkUpload\([^\)]*\)\s*\{(.*?\n\})",
            app_js,
            re.DOTALL,
        )
        assert match is not None, "enqueueChunkUpload body not found"
        body = match.group(1)
        assert "CHUNK_RETRY_DELAYS_MS" in body
        # Exhausting every retry must flag the take and warn the user
        # rather than discarding the chunk with no trace.
        assert "state.hadDroppedChunk = true" in body
        assert "showToast(" in body

    def test_build_version_line_wired(self, app_js: str):
        """The SPA must fetch /api/version on boot and render it into the
        #buildInfo line so the loaded build is glanceable (issue #13)."""
        assert "function loadVersion" in app_js
        assert "loadVersion()" in app_js
        assert "/api/version" in app_js
        assert "buildInfo" in app_js

    def test_record_flow_self_identifies_source(self, app_js: str):
        """The webapp's record flow must tag its created sessions with
        source 'webapp' so History can tell UI-dictated takes apart from
        externally-sourced (session-API consumer) ones (issue #59)."""
        assert "source: 'webapp'" in app_js

    def test_history_renders_source_badge(self, app_js: str):
        """The History list must render the per-session source attribution
        badge (issue #59)."""
        assert "source-badge" in app_js
        assert "s.source" in app_js

    def test_model_route_indicator_wired(self, app_js: str):
        """The Record tab must surface which STT backend/host actually
        served the last take (issue #156) — read from the /finish response
        and rendered into #modelRoute."""
        assert "function updateModelRoute" in app_js
        assert "updateModelRoute(data.served_model, data.served_host)" in app_js
        assert "els.modelRoute" in app_js

    def test_save_transcript_preserves_icon_markup(self, app_js: str):
        """`onSaveTranscript` must swap `innerHTML`, not `textContent` — the
        idle label carries a leading `<svg>` sprite icon that `textContent`
        would delete permanently on the first Save (issue #178)."""
        match = re.search(
            r"function onSaveTranscript\([^\)]*\)\s*\{(.*?)\n\}",
            app_js,
            re.DOTALL,
        )
        assert match is not None, "onSaveTranscript body not found"
        body = match.group(1)
        assert "saveTranscript.innerHTML" in body
        assert "saveTranscript.textContent" not in body

    def test_password_prompt_deduplicates_reentrant_calls(self, app_js: str):
        """`promptForPassword` must return the same in-flight promise on a
        re-entrant call while the gate is already open, instead of
        attaching a second `submit` listener — an iOS PWA background/
        foreground cycle re-runs `loadConfig()` on every `visibilitychange`,
        each hitting a 401 while the dialog is still up (issue #178)."""
        match = re.search(
            r"function promptForPassword\([^\)]*\)\s*\{(.*?)\n\}",
            app_js,
            re.DOTALL,
        )
        assert match is not None, "promptForPassword body not found"
        body = match.group(1)
        assert "if (_pendingPrompt) return _pendingPrompt;" in body
        assert "_pendingPrompt = null;" in body

    def test_reset_guards_against_mid_recording_tap(self, app_js: str):
        """`onReset` must bail out unless the session is idle — otherwise a
        tap mid-recording nulls `state.sessionId` out from under the still-
        running MediaRecorder (issue #178)."""
        match = re.search(
            r"function onReset\([^\)]*\)\s*\{(.*?)\n\}",
            app_js,
            re.DOTALL,
        )
        assert match is not None, "onReset body not found"
        body = match.group(1)
        assert "state.mode !== 'idle'" in body or 'state.mode !== "idle"' in body

    def test_no_hardcoded_alias_list_in_offline_fallback(self, app_js: str):
        """`applyConfigDefaults` should not hardcode the alias list — the
        offline fallback should be empty so the model list always comes
        from /api/config (which reads the JSON sample). The webapp
        team's earlier check-in had a hardcoded array here; this test
        guards against regressing to that shape."""
        # Find the applyConfigDefaults block.
        # applyConfigDefaults is a top-level export in config.js, so its
        # closing brace sits at column 0 (`\n}`).
        match = re.search(
            r"function applyConfigDefaults\([^\)]*\)\s*\{(.*?)\n\}",
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
# Button-emphasis contract (issue #190).
# ---------------------------------------------------------------------------

@pytest.fixture
def styles_css() -> str:
    return (STATIC_DIR / "styles.css").read_text(encoding="utf-8")


@pytest.fixture
def index_html() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


class TestHistoryButtonEmphasis:
    """History reads like Record: ghost by default, accent-tinted only on
    the contextually-next action (issue #190). The tiers are markup-level,
    so a rename or a stray `button-tint` regresses it silently."""

    def test_only_refresh_is_tinted_in_the_history_toolbar(self, index_html: str):
        toolbar = re.search(
            r'<div class="history-actions">(.*?)</div>', index_html, re.S
        )
        assert toolbar, "history-actions block not found"
        assert 'id="refreshHistory" type="button" class="button-tint compact"' in toolbar.group(1)
        for quiet in ("copySelection", "cleanAll"):
            assert f'id="{quiet}" type="button" class="button-ghost compact"' in toolbar.group(1)
        assert toolbar.group(1).count("button-tint") == 1

    def test_load_more_is_ghost(self, index_html: str):
        assert 'id="loadMoreHistory" type="button" class="button-ghost compact load-more-btn"' in index_html

    def test_history_rows_render_ghost_buttons(self, app_js: str):
        """Every per-row action is ghost in the markup; only the newest
        row's Copy is re-tinted, and that happens in CSS via :first-child
        so it survives Load more / delete-refresh with no JS state."""
        assert "copyBtn.className = 'button-ghost compact history-copy';" in app_js
        assert "reBtn.className = 'button-ghost compact';" in app_js
        assert "delBtn.className = 'button-ghost compact';" in app_js
        assert "button-tint" not in app_js

    def test_newest_row_copy_is_tinted_but_yields_to_the_copied_flash(
        self, styles_css: str
    ):
        assert ".history-list li:first-child .history-copy:not(.copied)" in styles_css
        # The green/red flashes must reach the ghost tier too.
        assert ".button-ghost.compact.copied" in styles_css
        assert ".button-ghost.compact.danger-flash" in styles_css

    def test_source_badge_has_one_style_for_every_source(
        self, styles_css: str, app_js: str
    ):
        """The accent is reserved for the next action, so no source
        attribution badge competes with it."""
        assert "source-badge.external" not in styles_css
        assert "' external'" not in app_js

    def test_settings_values_are_right_aligned_and_save_matches_load_more(
        self, styles_css: str
    ):
        assert ".settings-card .select-native { text-align: right; }" in styles_css
        assert ".settings-card .big-btn { padding: 10px var(--gap); min-height: 40px; }" in styles_css


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
