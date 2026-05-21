# Self-booting e2e harness + verify-before-ship pre-ship gate

**Issue:** #17 · **Date:** 2026-05-21

## What was done

Wired the test suites into a single pre-ship gate, matching app-launcher
(its #33). Before this, `tests/e2e/conftest.py` required a live tray on
:8443 and skipped the whole suite otherwise — a forgotten tray let a
regression ship while the run looked green.

### A. Self-booting e2e fixture (opt-in)

`tests/e2e/conftest.py` gained an autoboot mode, enabled with
`--e2e-autoboot` or `VT_E2E_AUTOBOOT=1`:

- `_free_tcp_port()` picks a free port — never the dev tray's :8443.
- `_autoboot_server` spawns `uvicorn app.webapp.server:app` as a
  subprocess (HTTPS, reusing `webapp/certificates/`; plain HTTP if the
  checkout has no certs), captured to `webapp/e2e-autoboot-webapp.log`.
- Polls `/healthz` until 200 (20 s). **A boot failure is a hard
  `pytest.fail`, never a skip** — that is the entire point of the gate.
- On Windows, spawned with `CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW`;
  torn down with `CTRL_BREAK` → terminate → kill on session teardown.

The default ad-hoc path (`_require_live_tray` on :8443) is unchanged;
autoboot is the pre-ship path and the two coexist. Precedence in
`base_url`: an explicit `VT_E2E_BASE_URL` > autoboot > live tray.

### B. `scripts/verify-before-ship.ps1`

A single pre-ship entry point that, exiting non-zero on the first failure:

1. `py_compile` / `compileall` over `app src tests`.
2. `pytest -q --ignore=tests/e2e` — the unit + API suite.
3. `pytest tests/e2e -q` with `VT_E2E_AUTOBOOT=1` — Chromium + WebKit.
4. Prints total wall time and `✅ Ready to ship.`

Re-runnable with no manual cleanup — teardown is the fixture's job.

### C. `CLAUDE.md` gate

The Verification section now requires `scripts/verify-before-ship.ps1`
for any change touching `app/webapp/` or webapp-facing `src/` modules,
and the "This repository" section points at it.

### D. Real-iPhone debugging doc

`docs/2026-05-21-iphone-debugging.md` — how to attach Edge/Chrome
DevTools to a live iPhone Safari session via `ios-webkit-debug-proxy`,
for the residual iOS-shell bugs WebKit-on-Windows can't reproduce. Pure
reference, no code.

## Files modified

- `tests/e2e/conftest.py` — autoboot fixture + `--e2e-autoboot` option
- New: `scripts/verify-before-ship.ps1`
- New: `docs/2026-05-21-iphone-debugging.md`
- `CLAUDE.md` — Verification clause + "This repository" pointer
- `README.md` — "Verifying changes before ship" section + autoboot note

## Validation

- `pwsh -File scripts/verify-before-ship.ps1` — green: compileall, then
  270 passed (non-e2e), then 18 passed / 8 skipped (e2e autoboot),
  `✅ Ready to ship` in ~20 s.
- Run **5× consecutively** — every run green, **zero orphaned uvicorn
  processes / stray listeners** afterwards.
- Autoboot picks a free port; a tray on :8443 is never touched.
