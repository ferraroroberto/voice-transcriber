# Playwright WebKit + iPhone projection & regression tests

**Issue:** #16 · **Date:** 2026-05-21

## What was done

Brought the e2e suite up to app-launcher parity (its #31 + #32).

### Phase 1 — WebKit + iPhone projection

- Installed the Playwright WebKit browser; `requirements-dev.txt` and
  the README e2e section now say `playwright install chromium webkit`.
- `tests/e2e/conftest.py` `pytest_configure` defaults `--browser` to
  **both** `chromium` and `webkit` when none is passed, so WebKit
  coverage can't be forgotten. `browser_context_args` merges the
  `iPhone 15 Pro Max` device descriptor into the WebKit context, so that
  projection runs as an iPhone-shaped Mobile-Safari-engine target.
- `browser_type_launch_args` adds Chromium fake-media-stream flags so
  the record-flow tests can drive a real `MediaRecorder` with no mic.
- A `desktop_only` marker + autouse skip fixture: tests needing the
  fake-media projection skip cleanly under WebKit.
- `VT_E2E_BASE_URL` env override on `base_url` — points the suite at any
  instance; default stays the live tray on :8443.
- `test_viewport.py` — asserts `window.innerWidth == 430` under WebKit,
  proving the iPhone descriptor actually applied.

### Phase 2 — regression tests for past iOS bugs

- `test_cache_busting.py` — pins issue #13's four cache-hygiene
  invariants (non-browser, runs once): `/` revalidates, static assets
  are immutable, the `?v=` stamps in the served HTML match the on-disk
  SHA-256 prefixes, `/api/version` matches the `app.js` stamp.
- `test_background_finalize.py` — pins issue #12: backgrounding mid-record
  via `pagehide` and via `visibilitychange`→hidden both finalise the
  take (a `/finish` POST fires and the "Saved while you were away"
  status renders — the signal that only `finalizeForBackground()`
  produces).
- `test_resume_take.py` — pins issue #14: after a backgrounded take the
  ▶ Resume button appears, and clicking it restarts recording with the
  earlier transcript still in place.

The record-flow tests route-mock the `/api/sessions*` family
(`tests/e2e/_helpers.py`) so they run deterministically without a
whisper round-trip, and disable VAD auto-stop so the take ends only via
the backgrounding path under test.

## Files modified

- `tests/e2e/conftest.py`
- New: `tests/e2e/_helpers.py`, `test_viewport.py`, `test_cache_busting.py`,
  `test_background_finalize.py`, `test_resume_take.py`
- `requirements-dev.txt`, `README.md`

## Validation

- `playwright install webkit` — exit 0.
- `pytest tests/e2e` against a throwaway server (both projections):
  **18 passed, 8 skipped** (skips are the projection exclusions —
  non-browser / WebKit-only / desktop-only). 5 consecutive runs, no
  flakiness, ~10 s wall time.
- Revert-check: neutering `finalizeForBackground()` in the
  `visibilitychange` / `pagehide` handlers makes the background-finalize
  and resume regression tests fail (status text + Resume button never
  appear); restored after.
- `pytest -q --ignore=tests/e2e` — still green.
