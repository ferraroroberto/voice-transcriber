# Project Instructions

Canonical instructions for AI coding agents working in this repository. Claude Code reads this file directly as project memory. Other agents (Cursor, Codex, etc.) reach it via the one-line `AGENTS.md` pointer.

## This repository
Tray-resident local voice-to-text app powered by a bundled whisper.cpp server, with a global hotkey workflow.
See `README.md` for setup, layout, and usage.

## Internal architecture
`docs/architecture.mmd` is a hand-authored Mermaid diagram of this repo's own internal structure — key modules/scripts (launcher, CLI, tray, GUI, webapp, `src/` logic layer), data flow, and external dependencies (whisper.cpp, local-llm-hub, Cloudflare). Companion to the fleet-wide convention in `ferraroroberto/fleet-config#256` — this repo's own shape isn't crawlable, so it's kept correct by discipline: **update the diagram in the same PR as any material structural change** (a new UI surface, a router added or moved, a `src/` module relocated).

**CI runtime baseline:** the `e2e` workflow's typical green run is ~2m35s (median over the 10 most recent successful runs); investigate at >6 min, and the job self-caps at `timeout-minutes: 15` so a wedge fails fast instead of running toward GitHub's 6h default ceiling.

Before declaring any webapp-touching change done, run the pre-ship gate:
`C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -File scripts/verify-before-ship.ps1`
(never bare `pwsh` — it's a 0-byte WindowsApps reparse stub on this machine that
fails non-interactively).

**Restart and verify before hand-off:** the canonical restart is **`tray.bat --restart`** — the orphan-proof reclaim-then-start that kills the tray subtree, then reclaims the webapp port `:8443` by PID scoped to this repo's `.venv` (CommandLine-matched), then starts fresh. It deliberately does **not** touch `:8090`/`:8091` (whisper-server / translate-server, mutex-shared with `local-llm-hub`). Run that, don't hand-roll the kill (a by-hand kill misses an orphaned port holder). As a by-hand fallback only, kill the process listening on `:8443` (`Get-NetTCPConnection -LocalPort 8443`) — never a blanket `pythonw`/`python` kill, sister apps and the shared whisper/translate servers must survive — then relaunch via `tray.bat`. **Confirm the new build is live** via `GET http://127.0.0.1:8443/api/version` (`git_sha` should match `HEAD`) or `GET /healthz` for liveness; don't leave a stale process serving.

## UX surface
*The design-conformance gate the `/issue-{start,finish,yolo}` skills read (convention: `project-scaffolding#83`). This is a live, parseable block — the product is the FastAPI + static PWA under `app/webapp/`.*

- design spec applies: yes        # `no` would make the gate a permanent no-op; this repo serves a real PWA
- paths:
  - app/webapp/static/**/*.css
  - app/webapp/static/**/*.{js,html}
- key views:                      # single tabbed SPA served at `/`
  - /          (Record · History · Settings bottom-tab panes, vendored fleet nav)
