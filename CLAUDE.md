# Project Instructions

Canonical instructions for AI coding agents working in this repository. Claude Code reads this file directly as project memory. Other agents (Cursor, Codex, etc.) reach it via the one-line `AGENTS.md` pointer.

## This repository
Tray-resident local voice-to-text app powered by a bundled whisper.cpp server, with a global hotkey workflow.
See `README.md` for setup, layout, and usage.

## Internal architecture
`docs/architecture.mmd` is a hand-authored Mermaid diagram of this repo's internal structure — key modules/scripts (launcher, CLI, tray, GUI, webapp, `src/` logic layer), data flow, external dependencies (whisper.cpp, local-llm-hub, Cloudflare); convention: `ferraroroberto/fleet-config#256`. Not crawlable, so **update it in the same PR as any material structural change** (new UI surface, router added or moved, `src/` module relocated).

**CI:** the `e2e` workflow's typical green run is ~2m35s; investigate at >6 min; the job self-caps at `timeout-minutes: 15`.

Before declaring any webapp-touching change done, run the pre-ship gate:
`C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -File scripts/verify-before-ship.ps1`
(never bare `pwsh` — a 0-byte WindowsApps reparse stub that fails non-interactively).

**Restart and verify before hand-off:** the canonical restart is **`tray.bat --restart`** — kills the tray subtree, reclaims webapp port `:8443` by PID scoped to this repo's `.venv` (CommandLine-matched), then starts fresh. It deliberately does **not** touch `:8090`/`:8091` (whisper-server / translate-server, mutex-shared with `local-llm-hub`). Don't hand-roll the kill (misses an orphaned port holder). By-hand fallback only: kill the process listening on `:8443` (`Get-NetTCPConnection -LocalPort 8443`) — never a blanket `pythonw`/`python` kill (sister apps and the shared whisper/translate servers must survive) — then relaunch via `tray.bat`. **Confirm the new build is live** via `GET http://127.0.0.1:8443/api/version` (`git_sha` should match `HEAD`) or `GET /healthz`; don't leave a stale process serving.

## UX surface
*The design-conformance gate the `/issue-{start,finish,yolo}` skills read (convention: `project-scaffolding#83`). This is a live, parseable block — the product is the FastAPI + static PWA under `app/webapp/`.*

- design spec applies: yes        # `no` would make the gate a permanent no-op; this repo serves a real PWA
- paths:
  - app/webapp/static/**/*.css
  - app/webapp/static/**/*.{js,html}
- key views:                      # single tabbed SPA served at `/`
  - /          (Record · History bottom-tab panes + Settings from the header gear, vendored fleet nav)
