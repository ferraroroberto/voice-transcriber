# Project Instructions

Canonical instructions for AI coding agents working in this repository. Claude Code reads this file directly as project memory. Other agents (Cursor, Codex, etc.) reach it via the one-line `AGENTS.md` pointer.

## This repository
Tray-resident local voice-to-text app powered by a bundled whisper.cpp server, with a global hotkey workflow.
See `README.md` for setup and usage; it indexes the topic docs under `docs/` (layout in `docs/layout.md`).

## Internal architecture
`docs/architecture.mmd` is a hand-authored Mermaid diagram of this repo's internal structure — key modules/scripts (launcher, CLI, tray, GUI, webapp, `src/` logic layer), data flow, external dependencies (whisper.cpp, local-llm-hub, Cloudflare); convention: `ferraroroberto/fleet-config#256`. Not crawlable, so **update it in the same PR as any material structural change** (new UI surface, router added or moved, `src/` module relocated).

**Design inventory (fleet-config#1383):** every surface an e2e test opens carries `surface(page, "<tab>.<surface>")` (vendored `tests/e2e/_surface.py`, `[vendored] surface_capture`; `[design.review] theme_storage_key` lets it flip this app's explicit theme). It is inert on every normal run. `/design-review measure voice-transcriber --e2e` arms it on the disposable instance `scripts/design_review_synthetic.py` boots (declared in `.fleet.toml [design.review.synthetic]`; the suite runs with `--e2e-autoboot` so the live-tray guard never fires) and keeps only the tests whose **own body** calls `surface(`. Put the call **after** the test's assertions: the capture flips the theme in place and a colour transition in flight reads as the other theme to a later computed-style assertion. Where both projections run, the first one to reach a surface wins (Chromium desktop).

**CI:** the `e2e` workflow's typical green run is ~2m35s; investigate at >6 min; the job self-caps at `timeout-minutes: 15`.

The pre-ship gate also runs eight ratchets: vendored `scripts/<name>_ratchet.py`, rules in `.fleet.toml [<name>_ratchet]`, baseline `.<name-with-hyphens>-ratchet-baseline.json` (e.g. `.doc-refs-ratchet-baseline.json`). All read the whole tree, so none is path-routed; all but structure run through `python -m scripts.<name>_ratchet_gate` (structure runs the script directly); exit 3 (a detector could not run, or a file could not be read, parsed, scanned or measured) fails as NOT CONFIRMED, never a pass or a skip. Fix at the source rather than baselining; shrink a baseline in the PR that removes the debt (`--update-baseline`); adding needs `--allow-additions` and shows in review.
- **static**: a duplicate block (jscpd via `npx`, so Node is a prerequisite) or a ruff finding (unused import/variable `F401,F841`, bugbear `B`, security `S` with `S113` strict) not in the baseline.
- **conventions**: three fleet conventions as `ast` rules — `no-window` (a `subprocess` spawn without `creationflags`), `logging-not-print` (a `print` in `app/` or `src/` library code) and `no-drive-path` (a string literal starting with a drive letter); fails when a file's hit count for a rule rises above the baseline. `secret-compare` and `git-wrapper` are off because the repo has no such helper. A call that must break a rule keeps a reasoned `# <rule>-exempt: <why>` marker.
- **comment**: a file's count of `#NNN` issue references inside `.py`, `.js`, `.css` or `.ps1` comments rising above the baseline; a comment states the present-tense why, history lives in the issue, the PR and `git log`; tests and vendored copies are out of scope. Reword the comment.
- **instruction**: the fleet prompt audit's deterministic rules over this file and `AGENTS.md` (no skills shipped); fails when a rule's violation count rises, or a file passes its size cap (200 lines for this file). Reword the flagged line.
- **doc-refs**: `README.md`, `CLAUDE.md` or `docs/**/*.md` naming a repo path, `python -m` module, relative link or loopback port that no longer resolves against the tracked tree and the app's `port`. The other services' ports (hub, whisper, translate, the iPhone DevTools proxy) sit in `allow_refs`; a new one is added there. Point the reference at what exists now.
- **dead-code**: a function, class or method not read by name anywhere in the tracked Python tree (tests included). Route handlers, dunders, test hooks and `__all__` are built-in entry points; a real entry point the rules miss goes in `allow` or `entry_decorators` in `.fleet.toml`. Delete the definition or call it.
- **docs**: `README.md` past 40 KiB or its baselined size, or `README.md` / `docs/**/*.md` gaining an issue reference, a dated heading or a changelog heading.
- **structure**: shape `webapp-tray`, `extra_roots = ["webapp"]`; a file past 1,000 lines, a Python/JS function past 100 lines, a directory past 40 modules or a `src` module importing `app`, beyond the baseline; a tracked root folder outside the shape fails too (never baselined). New code goes where `docs/layout.md` says: split rather than grow a baselined offender.

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
