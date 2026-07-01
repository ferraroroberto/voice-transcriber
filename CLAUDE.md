# Project Instructions

Canonical instructions for AI coding agents working in this repository. Claude Code reads this file directly as project memory. Other agents (Cursor, Codex, etc.) reach it via the one-line `AGENTS.md` pointer.

## This repository
Tray-resident local voice-to-text app powered by a bundled whisper.cpp server, with a global hotkey workflow.
See `README.md` for setup, layout, and usage.

Before declaring any webapp-touching change done, run the pre-ship gate:
`pwsh -File scripts/verify-before-ship.ps1`.

**Restart and verify before hand-off:** the canonical restart is **`tray.bat --restart`** — the orphan-proof reclaim-then-start that kills the tray subtree, then reclaims the webapp port `:8443` by PID scoped to this repo's `.venv` (CommandLine-matched), then starts fresh. It deliberately does **not** touch `:8090`/`:8091` (whisper-server / translate-server, mutex-shared with `claude-local-calls`). Run that, don't hand-roll the kill (a by-hand kill misses an orphaned port holder). As a by-hand fallback only, kill the process listening on `:8443` (`Get-NetTCPConnection -LocalPort 8443`) — never a blanket `pythonw`/`python` kill, sister apps and the shared whisper/translate servers must survive — then relaunch via `tray.bat`. **Confirm the new build is live** via `GET http://127.0.0.1:8443/api/version` (`git_sha` should match `HEAD`) or `GET /healthz` for liveness; don't leave a stale process serving.

## UX surface
*The design-conformance gate the `/issue-{start,finish,yolo}` skills read (convention: `project-scaffolding#83`). This is a live, parseable block — the product is the FastAPI + static PWA under `app/webapp/`.*

- design spec applies: yes        # `no` would make the gate a permanent no-op; this repo serves a real PWA
- paths:
  - app/webapp/static/**/*.css
  - app/webapp/static/**/*.{js,html}
- key views:                      # single tabbed SPA served at `/`
  - /          (record → history → settings panels)
